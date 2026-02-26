import os
import random
import uuid
import time
import threading
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'ludo-royal-2024')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ─────────────────────────────────────────────
# LUDO CONSTANTS
# ─────────────────────────────────────────────

COLORS = ['red', 'blue', 'green', 'yellow']

# The 52-cell shared outer path (clockwise)
PATH = [
    [6,1],[6,2],[6,3],[6,4],[6,5],          # 0-4   red start area
    [5,6],[4,6],[3,6],[2,6],[1,6],[0,6],     # 5-10
    [0,7],                                    # 11
    [0,8],[1,8],[2,8],[3,8],[4,8],[5,8],     # 12-17  blue start area
    [6,9],[6,10],[6,11],[6,12],[6,13],[6,14],# 18-23
    [7,14],                                   # 24
    [8,14],[8,13],[8,12],[8,11],[8,10],[8,9],# 25-30  green start area
    [9,8],[10,8],[11,8],[12,8],[13,8],[14,8],# 31-36
    [14,7],                                   # 37
    [14,6],[13,6],[12,6],[11,6],[10,6],[9,6],# 38-43  yellow start area
    [8,5],[8,4],[8,3],[8,2],[8,1],[8,0],     # 44-49
    [7,0],                                    # 50
    [6,0],                                    # 51
]

# Each color's 6-cell home stretch (the colored corridor to center)
HOME_STRETCH = {
    'red':    [[7,1],[7,2],[7,3],[7,4],[7,5],[7,6]],
    'blue':   [[1,7],[2,7],[3,7],[4,7],[5,7],[6,7]],
    'green':  [[13,7],[12,7],[11,7],[10,7],[9,7],[8,7]],
    'yellow': [[7,13],[7,12],[7,11],[7,10],[7,9],[7,8]],
}

# Where each color's token enters the board (PATH index)
START_IDX = {
    'red':    0,
    'blue':   13,
    'green':  26,
    'yellow': 39,
}

# The PATH index just BEFORE entering the home stretch
# After reaching this cell, next move enters home_stretch[0]
ENTRY_BEFORE_HOME = {
    'red':    51,   # PATH[51] = [6,0], then home stretch starts
    'blue':   11,   # PATH[11] = [0,7]
    'green':  37,   # PATH[37] = [14,7]
    'yellow': 24,   # PATH[24] = [7,14]
}

# Safe squares on the outer path (cannot be captured here)
SAFE_SQUARES = {0, 8, 13, 21, 26, 34, 39, 47}

# ─────────────────────────────────────────────
# TOKEN STATE
# pos == -1        → token is at home base (not on board)
# pos == 0..51     → token is on the outer PATH at index pos
# stretch == 0..5  → token is in the home stretch at index stretch
# finished == True → token reached center (done)
# ─────────────────────────────────────────────

def make_token(idx):
    return {
        'id': idx,
        'pos': -1,        # -1 = home base
        'stretch': -1,    # -1 = not in home stretch
        'finished': False
    }

def make_player(color, is_cpu):
    return {
        'color': color,
        'is_cpu': is_cpu,
        'tokens': [make_token(i) for i in range(4)],
        'finished_count': 0,
        'sid': None,
    }


# ─────────────────────────────────────────────
# CORE LOGIC: can a token move with this dice?
# ─────────────────────────────────────────────
def can_move(token, dice, color):
    # RULE: only dice=6 allows moving any token
    if dice != 6:
        return False
    if token['finished']:
        return False

    # Token at home base: 6 brings it out
    if token['pos'] == -1:
        return True

    # Token in home stretch: move 6 steps, can't overshoot position 5
    if token['stretch'] >= 0:
        new_stretch = token['stretch'] + dice
        return new_stretch <= 5

    # Token on outer path: move 6 steps
    # Check it won't overshoot home stretch
    entry = ENTRY_BEFORE_HOME[color]
    steps_to_entry = (entry - token['pos']) % 52
    if steps_to_entry == 0:
        steps_to_entry = 52
    if dice <= steps_to_entry:
        return True
    steps_into_stretch = dice - steps_to_entry - 1
    return steps_into_stretch <= 5


# ─────────────────────────────────────────────
# CORE LOGIC: apply a move
# ─────────────────────────────────────────────
def apply_move(game, player_idx, token_idx):
    player = game['players'][player_idx]
    token  = player['tokens'][token_idx]
    color  = player['color']
    dice   = game['dice_value']
    events = []

    # ── Case 1: Token at home base → bring it out ──
    if token['pos'] == -1:
        # Must have rolled 6 (already validated in can_move)
        token['pos'] = START_IDX[color]
        token['stretch'] = -1
        cap = check_capture(game, player_idx, token)
        if cap:
            events.append({'type': 'capture', 'by': color, 'victim': cap})
        return events

    # ── Case 2: Token already in home stretch ──
    if token['stretch'] >= 0:
        new_stretch = token['stretch'] + dice
        if new_stretch == 5:
            # Reached center exactly
            token['stretch'] = 5
            token['finished'] = True
            player['finished_count'] += 1
            events.append({'type': 'home', 'color': color})
            if player['finished_count'] == 4:
                events.append({'type': 'win', 'color': color})
        elif new_stretch < 5:
            token['stretch'] = new_stretch
        # new_stretch > 5 is blocked by can_move, should never happen
        return events

    # ── Case 3: Token on outer path ──
    entry = ENTRY_BEFORE_HOME[color]
    steps_to_entry = (entry - token['pos']) % 52
    if steps_to_entry == 0:
        steps_to_entry = 52

    if dice <= steps_to_entry:
        # Move on outer path
        token['pos'] = (token['pos'] + dice) % 52
        cap = check_capture(game, player_idx, token)
        if cap:
            events.append({'type': 'capture', 'by': color, 'victim': cap})
    else:
        # Enter home stretch
        steps_into_stretch = dice - steps_to_entry - 1
        token['pos'] = -2  # mark as "in stretch" (not on outer path)
        token['stretch'] = steps_into_stretch
        if token['stretch'] == 5:
            token['finished'] = True
            player['finished_count'] += 1
            events.append({'type': 'home', 'color': color})
            if player['finished_count'] == 4:
                events.append({'type': 'win', 'color': color})

    return events


def check_capture(game, attacker_idx, token):
    """If attacker's token lands on an enemy token, send it home."""
    pos = token['pos']
    # Only capture on outer path
    if pos < 0 or token['stretch'] >= 0:
        return None
    # No capture on safe squares
    if pos in SAFE_SQUARES:
        return None
    for i, p in enumerate(game['players']):
        if i == attacker_idx:
            continue
        for t in p['tokens']:
            if t['pos'] == pos and t['stretch'] < 0 and not t['finished']:
                # Send it back home
                t['pos'] = -1
                t['stretch'] = -1
                return p['color']
    return None


# ─────────────────────────────────────────────
# CPU AI
# ─────────────────────────────────────────────
def cpu_choose_token(game, player_idx):
    player  = game['players'][player_idx]
    color   = player['color']
    dice    = game['dice_value']
    movable = [t for t in player['tokens'] if can_move(t, dice, color)]
    if not movable:
        return None

    # Priority 1: capture an enemy
    for t in movable:
        if t['pos'] >= 0 and t['stretch'] < 0:
            entry = ENTRY_BEFORE_HOME[color]
            steps = (entry - t['pos']) % 52 or 52
            if dice <= steps:
                new_pos = (t['pos'] + dice) % 52
                if new_pos not in SAFE_SQUARES:
                    for p2 in game['players']:
                        if p2['color'] == color: continue
                        if any(ot['pos'] == new_pos and ot['stretch'] < 0 and not ot['finished'] for ot in p2['tokens']):
                            return t['id']

    # Priority 2: bring token out on 6
    if dice == 6:
        for t in movable:
            if t['pos'] == -1:
                return t['id']

    # Priority 3: advance the token closest to home
    on_board = [t for t in movable if t['pos'] >= 0 or t['stretch'] >= 0]
    if on_board:
        # Pick the one furthest along
        def progress(t):
            if t['stretch'] >= 0:
                return 1000 + t['stretch']
            color_start = START_IDX[color]
            return (t['pos'] - color_start) % 52
        return max(on_board, key=progress)['id']

    return movable[0]['id']


# ─────────────────────────────────────────────
# ROOM / LOBBY MANAGEMENT
# ─────────────────────────────────────────────
rooms    = {}  # room_id -> game dict
players  = {}  # sid -> {room_id, player_idx}
mm_queue = []  # sids waiting for quick match


def create_room(mode):
    room_id = str(uuid.uuid4())[:8].upper()
    cpu_flags = {
        '4p':  [False, False, False, False],
        '1v3': [False, True,  True,  True ],
        '2v2': [False, False, True,  True ],
        '3v1': [False, False, False, True ],
    }.get(mode, [False]*4)

    game = {
        'room_id':        room_id,
        'mode':           mode,
        'players':        [make_player(COLORS[i], cpu_flags[i]) for i in range(4)],
        'current_player': 0,
        'dice_value':     0,
        'rolled':         False,
        'game_over':      False,
        'winner':         None,
        'human_slots':    [i for i, c in enumerate(cpu_flags) if not c],
        'filled_slots':   [],
        'started':        False,
    }
    rooms[room_id] = game
    return room_id


def game_to_client(game, events=None):
    payload = {
        'room_id':        game['room_id'],
        'mode':           game['mode'],
        'players':        game['players'],
        'current_player': game['current_player'],
        'dice_value':     game['dice_value'],
        'rolled':         game['rolled'],
        'game_over':      game['game_over'],
        'winner':         game['winner'],
        'started':        game['started'],
        'human_slots':    game['human_slots'],
        'filled_slots':   game['filled_slots'],
    }
    if events:
        payload['events'] = events
    return payload


def broadcast(room_id, events=None):
    game = rooms.get(room_id)
    if game:
        socketio.emit('game_state', game_to_client(game, events), room=room_id)


def start_cpu_turn(room_id, delay=1.2):
    def run():
        time.sleep(delay)
        game = rooms.get(room_id)
        if not game or game['game_over'] or game['rolled']:
            return
        cp = game['players'][game['current_player']]
        if not cp['is_cpu']:
            return

        # Roll dice
        val = random.randint(1, 6)
        game['dice_value'] = val
        game['rolled']     = True
        broadcast(room_id)

        color   = cp['color']
        movable = [t for t in cp['tokens'] if can_move(t, val, color)]

        if not movable:
            socketio.emit('notification',
                {'msg': f"{color.upper()} rolled {val} — needs 6 to move!"},
                room=room_id)
            time.sleep(1.0)
            next_turn(room_id, advance=True)  # always advance if no moves
            return

        time.sleep(0.8)
        tok_id = cpu_choose_token(game, game['current_player'])
        events = apply_move(game, game['current_player'], tok_id)

        win = next((e for e in events if e['type'] == 'win'), None)
        if win:
            game['game_over'] = True
            game['winner']    = win['color']
            broadcast(room_id, events)
            return

        broadcast(room_id, events)
        time.sleep(0.5)
        next_turn(room_id, advance=val != 6)

    threading.Thread(target=run, daemon=True).start()


def next_turn(room_id, advance=True):
    game = rooms.get(room_id)
    if not game or game['game_over']:
        return
    game['rolled'] = False
    if advance:
        game['current_player'] = (game['current_player'] + 1) % 4
    broadcast(room_id)
    cp = game['players'][game['current_player']]
    if cp['is_cpu']:
        start_cpu_turn(room_id)


# ─────────────────────────────────────────────
# SOCKET EVENTS
# ─────────────────────────────────────────────

@socketio.on('connect')
def on_connect():
    print(f"[+] {request.sid}")


@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    if sid in mm_queue:
        mm_queue.remove(sid)
    info = players.pop(sid, None)
    if info:
        game = rooms.get(info['room_id'])
        if game:
            pidx = info['player_idx']
            game['players'][pidx]['sid'] = None
            game['players'][pidx]['is_cpu'] = True
            broadcast(info['room_id'])
    print(f"[-] {sid}")


@socketio.on('create_room')
def on_create_room(data):
    mode    = data.get('mode', '1v3')
    room_id = create_room(mode)
    game    = rooms[room_id]
    slot    = game['human_slots'][0]

    game['players'][slot]['sid'] = request.sid
    game['filled_slots'].append(slot)
    players[request.sid] = {'room_id': room_id, 'player_idx': slot}
    join_room(room_id)
    emit('joined', {'room_id': room_id, 'player_idx': slot, 'color': COLORS[slot]})

    if len(game['human_slots']) == 1:
        game['started'] = True
        broadcast(room_id)
        cp = game['players'][game['current_player']]
        if cp['is_cpu']:
            start_cpu_turn(room_id)
    else:
        broadcast(room_id)


@socketio.on('join_room')
def on_join_room(data):
    room_id = data.get('room_id', '').upper().strip()
    game    = rooms.get(room_id)
    if not game:
        emit('error', {'msg': 'Room not found!'}); return
    if game['started']:
        emit('error', {'msg': 'Game already started!'}); return
    open_slots = [s for s in game['human_slots'] if s not in game['filled_slots']]
    if not open_slots:
        emit('error', {'msg': 'Room is full!'}); return

    slot = open_slots[0]
    game['players'][slot]['sid'] = request.sid
    game['filled_slots'].append(slot)
    players[request.sid] = {'room_id': room_id, 'player_idx': slot}
    join_room(room_id)
    emit('joined', {'room_id': room_id, 'player_idx': slot, 'color': COLORS[slot]})

    if set(game['human_slots']) == set(game['filled_slots']):
        game['started'] = True
    broadcast(room_id)
    if game['started']:
        cp = game['players'][game['current_player']]
        if cp['is_cpu']:
            start_cpu_turn(room_id)


@socketio.on('roll_dice')
def on_roll_dice(data):
    info = players.get(request.sid)
    if not info: return
    game = rooms.get(info['room_id'])
    pidx = info['player_idx']
    if not game or game['game_over'] or game['rolled']: return
    if game['current_player'] != pidx:
        emit('error', {'msg': "Not your turn!"}); return

    val = random.randint(1, 6)
    game['dice_value'] = val
    game['rolled']     = True

    cp      = game['players'][pidx]
    color   = cp['color']
    movable = [t for t in cp['tokens'] if can_move(t, val, color)]

    broadcast(info['room_id'])

    if not movable:
        socketio.emit('notification',
            {'msg': f"Rolled {val} — need a 6 to move! Next player..."},
            room=info['room_id'])
        time.sleep(0.8)
        next_turn(info['room_id'], advance=True)  # always advance if no moves


@socketio.on('move_token')
def on_move_token(data):
    info = players.get(request.sid)
    if not info: return
    game     = rooms.get(info['room_id'])
    pidx     = info['player_idx']
    token_id = data.get('token_id')
    if not game or game['game_over'] or not game['rolled']: return
    if game['current_player'] != pidx: return
    if token_id is None or not (0 <= token_id <= 3): return

    cp    = game['players'][pidx]
    token = cp['tokens'][token_id]
    if not can_move(token, game['dice_value'], cp['color']):
        emit('error', {'msg': 'Cannot move that token!'}); return

    events = apply_move(game, pidx, token_id)

    win = next((e for e in events if e['type'] == 'win'), None)
    if win:
        game['game_over'] = True
        game['winner']    = win['color']
        broadcast(info['room_id'], events)
        return

    broadcast(info['room_id'], events)
    next_turn(info['room_id'], advance=game['dice_value'] != 6)


@socketio.on('quick_join')
def on_quick_join(data):
    sid = request.sid
    if sid in mm_queue: return
    mm_queue.append(sid)
    for s in mm_queue:
        socketio.emit('matchmaking_count', {'count': len(mm_queue)}, to=s)
    if len(mm_queue) >= 4:
        four = mm_queue[:4]
        del mm_queue[:4]
        room_id = create_room('4p')
        game = rooms[room_id]
        game['human_slots'] = [0,1,2,3]
        for i, s in enumerate(four):
            game['players'][i]['is_cpu'] = False
            game['players'][i]['sid']    = s
            game['filled_slots'].append(i)
            players[s] = {'room_id': room_id, 'player_idx': i}
            join_room(room_id, sid=s)
            socketio.emit('joined', {'room_id': room_id, 'player_idx': i, 'color': COLORS[i]}, to=s)
        game['started'] = True
        broadcast(room_id)


@socketio.on('cancel_matchmaking')
def on_cancel_matchmaking(data):
    sid = request.sid
    if sid in mm_queue:
        mm_queue.remove(sid)


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/health')
def health():
    return {'status': 'ok', 'rooms': len(rooms)}

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)