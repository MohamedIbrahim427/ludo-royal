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
# CONSTANTS
# ─────────────────────────────────────────────
COLORS = ['red', 'blue', 'green', 'yellow']

PATH = [
    [6,1],[6,2],[6,3],[6,4],[6,5],
    [5,6],[4,6],[3,6],[2,6],[1,6],[0,6],[0,7],
    [0,8],[1,8],[2,8],[3,8],[4,8],[5,8],
    [6,9],[6,10],[6,11],[6,12],[6,13],[6,14],[7,14],
    [8,14],[8,13],[8,12],[8,11],[8,10],[8,9],
    [9,8],[10,8],[11,8],[12,8],[13,8],[14,8],[14,7],
    [14,6],[13,6],[12,6],[11,6],[10,6],[9,6],
    [8,5],[8,4],[8,3],[8,2],[8,1],[8,0],[7,0],[6,0],
]

HOME_STRETCH = {
    'red':    [[7,1],[7,2],[7,3],[7,4],[7,5],[7,6]],
    'blue':   [[1,7],[2,7],[3,7],[4,7],[5,7],[6,7]],
    'green':  [[13,7],[12,7],[11,7],[10,7],[9,7],[8,7]],
    'yellow': [[7,13],[7,12],[7,11],[7,10],[7,9],[7,8]],
}

START_IDX = {'red': 0, 'blue': 13, 'yellow': 26, 'green': 39}
ENTRY_BEFORE_HOME = {'red': 51, 'blue': 11, 'green': 37, 'yellow': 24}
SAFE_SQUARES = {0, 8, 13, 21, 26, 34, 39, 47}

# ─────────────────────────────────────────────
# TOKEN STATE
# pos == -1 → at home base (not on board)
# pos == 0..51 → on outer PATH at this index
# stretch == 0..5 → inside home stretch
# finished → reached center
# ─────────────────────────────────────────────

def make_token(idx):
    return {'id': idx, 'pos': -1, 'stretch': -1, 'finished': False}

def make_player(color, is_cpu):
    return {
        'color': color,
        'is_cpu': is_cpu,
        'tokens': [make_token(i) for i in range(4)],
        'finished_count': 0,
        'sid': None,
        'last_moved_token': None,          # track for triple-six penalty
    }

# ─────────────────────────────────────────────
# RULE 3 & 9: can this token move with this dice?
# ─────────────────────────────────────────────
def can_move(token, dice, color):
    if token['finished']:
        return False

    # Rule 3 & 9: token at home base needs a 6 to come out
    if token['pos'] == -1:
        return dice == 6

    # Rule 7: token in home stretch — need exact count, no overshooting
    if token['stretch'] >= 0:
        return token['stretch'] + dice <= 5

    # Token on outer path — check if it can move without overshooting home stretch
    entry = ENTRY_BEFORE_HOME[color]
    steps_to_entry = (entry - token['pos']) % 52   # distance to entry square (0 if already there)

    if dice <= steps_to_entry:
        return True   # stays on outer path or lands exactly on entry square

    # Would enter home stretch — check overshoot
    steps_into_stretch = dice - steps_to_entry - 1
    return steps_into_stretch <= 5

# ─────────────────────────────────────────────
# RULE 6: blocking — two same-color tokens on same square block opponents
# ─────────────────────────────────────────────
def is_blocked(game, attacker_idx, new_pos):
    """Returns True if new_pos is blocked by 2+ enemy tokens."""
    if new_pos < 0:
        return False
    for i, p in enumerate(game['players']):
        if i == attacker_idx:
            continue
        count = sum(1 for t in p['tokens']
                    if t['pos'] == new_pos and t['stretch'] < 0 and not t['finished'])
        if count >= 2:
            return True
    return False

# ─────────────────────────────────────────────
# APPLY A MOVE — returns list of events
# ─────────────────────────────────────────────
def apply_move(game, player_idx, token_idx):
    player = game['players'][player_idx]
    token  = player['tokens'][token_idx]
    color  = player['color']
    dice   = game['dice_value']
    events = []

    # Record that this token was moved (for triple-six penalty)
    player['last_moved_token'] = token_idx

    # ── Case 1: bring token out of home base (Rule 3) ──
    if token['pos'] == -1:
        new_pos = START_IDX[color]
        # Check if blocked by 2 enemy tokens (Rule 6)
        if is_blocked(game, player_idx, new_pos):
            events.append({'type': 'blocked', 'color': color})
            return events
        token['pos'] = new_pos
        token['stretch'] = -1
        cap = check_capture(game, player_idx, token)
        if cap:
            events.append({'type': 'capture', 'by': color, 'victim': cap})
        return events

    # ── Case 2: move inside home stretch (Rule 7) ──
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
        # new_stretch > 5 is prevented by can_move
        return events

    # ── Case 3: move on outer path ──
    entry = ENTRY_BEFORE_HOME[color]
    steps_to_entry = (entry - token['pos']) % 52

    if dice <= steps_to_entry:
        # Stays on outer path
        new_pos = (token['pos'] + dice) % 52
        # Check blocking (Rule 6)
        if is_blocked(game, player_idx, new_pos):
            events.append({'type': 'blocked', 'color': color})
            return events
        token['pos'] = new_pos
        cap = check_capture(game, player_idx, token)
        if cap:
            events.append({'type': 'capture', 'by': color, 'victim': cap})
    else:
        # Enter home stretch (Rule 7)
        steps_into_stretch = dice - steps_to_entry - 1
        token['pos'] = -2   # mark as in home stretch
        token['stretch'] = steps_into_stretch
        if token['stretch'] == 5:
            token['finished'] = True
            player['finished_count'] += 1
            events.append({'type': 'home', 'color': color})
            if player['finished_count'] == 4:
                events.append({'type': 'win', 'color': color})

    return events

# ─────────────────────────────────────────────
# RULE 5: capture
# ─────────────────────────────────────────────
def check_capture(game, attacker_idx, token):
    pos = token['pos']
    if pos < 0 or token['stretch'] >= 0:
        return None
    # No capture on safe squares (Rule 4)
    if pos in SAFE_SQUARES:
        return None
    for i, p in enumerate(game['players']):
        if i == attacker_idx:
            continue
        for t in p['tokens']:
            if t['pos'] == pos and t['stretch'] < 0 and not t['finished']:
                # Only capture if NOT a block (single token)
                count = sum(1 for ot in p['tokens']
                            if ot['pos'] == pos and ot['stretch'] < 0 and not ot['finished'])
                if count == 1:
                    t['pos'] = -1
                    t['stretch'] = -1
                    return p['color']
    return None

# ─────────────────────────────────────────────
# RULE 8: triple-6 rule (streak tracking)
# ─────────────────────────────────────────────
def check_triple_six(game, player_idx):
    """Returns True if this player just rolled their 3rd consecutive 6."""
    history = game.get('six_streak', {})
    streak  = history.get(player_idx, 0)
    if game['dice_value'] == 6:
        history[player_idx] = streak + 1
    else:
        history[player_idx] = 0
    game['six_streak'] = history
    return history.get(player_idx, 0) >= 3

# ─────────────────────────────────────────────
# CPU AI
# ─────────────────────────────────────────────
def cpu_choose_token(game, player_idx):
    player  = game['players'][player_idx]
    color   = player['color']
    dice    = game['dice_value']
    movable = [t for t in player['tokens']
               if can_move(t, dice, color) and not _would_block(game, player_idx, t, dice)]
    if not movable:
        # try including blocked moves
        movable = [t for t in player['tokens'] if can_move(t, dice, color)]
    if not movable:
        return None

    # Priority 1: capture an enemy
    for t in movable:
        if t['pos'] >= 0 and t['stretch'] < 0:
            entry = ENTRY_BEFORE_HOME[color]
            steps = (entry - t['pos']) % 52
            if dice <= steps:
                new_pos = (t['pos'] + dice) % 52
                if new_pos not in SAFE_SQUARES:
                    for p2 in game['players']:
                        if p2['color'] == color: continue
                        enemies = [ot for ot in p2['tokens']
                                   if ot['pos'] == new_pos and ot['stretch'] < 0 and not ot['finished']]
                        if len(enemies) == 1:
                            return t['id']

    # Priority 2: bring token out on 6
    if dice == 6:
        for t in movable:
            if t['pos'] == -1:
                return t['id']

    # Priority 3: advance furthest token
    on_board = [t for t in movable if t['pos'] >= 0 or t['stretch'] >= 0]
    if on_board:
        def progress(t):
            if t['stretch'] >= 0: return 1000 + t['stretch']
            return (t['pos'] - START_IDX[color]) % 52
        return max(on_board, key=progress)['id']

    return movable[0]['id']

def _would_block(game, player_idx, token, dice):
    """Check if the move result would be blocked by enemies."""
    color = game['players'][player_idx]['color']
    if token['pos'] == -1:
        new_pos = START_IDX[color]
        return is_blocked(game, player_idx, new_pos)
    if token['stretch'] >= 0:
        return False
    entry = ENTRY_BEFORE_HOME[color]
    steps = (entry - token['pos']) % 52
    if dice <= steps:
        new_pos = (token['pos'] + dice) % 52
        return is_blocked(game, player_idx, new_pos)
    return False

# ─────────────────────────────────────────────
# ROOM MANAGEMENT
# ─────────────────────────────────────────────
rooms    = {}
players  = {}
mm_queue = []

def create_room(mode):
    room_id   = str(uuid.uuid4())[:8].upper()
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
        'six_streak':     {},
        'extra_turn':     False,
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
        'extra_turn':     game.get('extra_turn', False),
    }
    if events:
        payload['events'] = events
    return payload

def broadcast(room_id, events=None):
    game = rooms.get(room_id)
    if game:
        socketio.emit('game_state', game_to_client(game, events), room=room_id)

# ─────────────────────────────────────────────
# TURN MANAGEMENT
# ─────────────────────────────────────────────
def next_turn(room_id, rolled_six=False):
    game = rooms.get(room_id)
    if not game or game['game_over']:
        return
    game['rolled'] = False

    # Rule 8: triple six → lose turn, send last moved token back
    if check_triple_six(game, game['current_player']):
        cp = game['players'][game['current_player']]
        # Send the last moved token back (if any)
        last_idx = cp.get('last_moved_token')
        if last_idx is not None:
            last_token = cp['tokens'][last_idx]
            if last_token['pos'] >= 0 or last_token['stretch'] >= 0:
                last_token['pos'] = -1
                last_token['stretch'] = -1
                # If the token was in home stretch and not finished, it's now back at start
        socketio.emit('notification',
            {'msg': f"3 sixes in a row! {cp['color'].upper()} loses their turn!"},
            room=room_id)
        game['six_streak'][game['current_player']] = 0
        rolled_six = False  # force advance (no extra turn despite rolling six)

    # Rule 8: rolled 6 → extra turn (same player)
    if rolled_six:
        game['extra_turn'] = True
        broadcast(room_id)
        cp = game['players'][game['current_player']]
        socketio.emit('notification',
            {'msg': f"🎲 {cp['color'].upper()} rolled 6 — EXTRA TURN!"},
            room=room_id)
        if cp['is_cpu']:
            start_cpu_turn(room_id, delay=1.0)
        return

    # Advance to next player
    game['extra_turn'] = False
    game['current_player'] = (game['current_player'] + 1) % 4
    broadcast(room_id)
    cp = game['players'][game['current_player']]
    if cp['is_cpu']:
        start_cpu_turn(room_id)

def start_cpu_turn(room_id, delay=1.2):
    def run():
        time.sleep(delay)
        game = rooms.get(room_id)
        if not game or game['game_over'] or game['rolled']:
            return
        cp = game['players'][game['current_player']]
        if not cp['is_cpu']:
            return

        # Roll
        val = random.randint(1, 6)
        game['dice_value'] = val
        game['rolled']     = True
        broadcast(room_id)

        color   = cp['color']
        movable = [t for t in cp['tokens'] if can_move(t, val, color)]

        if not movable:
            # Even if no moves, a 6 still grants an extra turn
            socketio.emit('notification',
                {'msg': f"{color.upper()} rolled {val} — no moves!"},
                room=room_id)
            time.sleep(1.0)
            next_turn(room_id, rolled_six=(val == 6))
            return

        time.sleep(0.7)
        tok_id = cpu_choose_token(game, game['current_player'])
        if tok_id is None:
            socketio.emit('notification',
                {'msg': f"{color.upper()} has no valid moves!"},
                room=room_id)
            time.sleep(0.8)
            next_turn(room_id, rolled_six=(val == 6))
            return

        events = apply_move(game, game['current_player'], tok_id)
        win    = next((e for e in events if e['type'] == 'win'), None)
        if win:
            game['game_over'] = True
            game['winner']    = win['color']
            broadcast(room_id, events)
            return

        broadcast(room_id, events)
        time.sleep(0.5)
        next_turn(room_id, rolled_six=(val == 6))

    threading.Thread(target=run, daemon=True).start()

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
            game['players'][pidx]['sid']    = None
            game['players'][pidx]['is_cpu'] = True
            # If it was this player's turn and game is active, start CPU turn
            if game['started'] and not game['game_over'] and game['current_player'] == pidx:
                # If they had already rolled, reset rolled flag so CPU can roll anew
                game['rolled'] = False
                start_cpu_turn(info['room_id'], delay=1.0)
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

    val   = random.randint(1, 6)
    game['dice_value'] = val
    game['rolled']     = True

    cp      = game['players'][pidx]
    color   = cp['color']
    movable = [t for t in cp['tokens'] if can_move(t, val, color)]

    broadcast(info['room_id'])

    # Rule 9: if no moves, still grant extra turn if rolled a 6
    if not movable:
        msg = f"Rolled {val} — need a 6 to move!" if val != 6 else f"Rolled 6 but all blocked!"
        socketio.emit('notification', {'msg': msg}, room=info['room_id'])
        time.sleep(0.6)
        next_turn(info['room_id'], rolled_six=(val == 6))

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

    events   = apply_move(game, pidx, token_id)
    dice_val = game['dice_value']

    win = next((e for e in events if e['type'] == 'win'), None)
    if win:
        game['game_over'] = True
        game['winner']    = win['color']
        broadcast(info['room_id'], events)
        return

    broadcast(info['room_id'], events)

    # Rule 8: rolled 6 → extra turn (even if move was invalid? but we already moved)
    next_turn(info['room_id'], rolled_six=(dice_val == 6))

@socketio.on('quick_join')
def on_quick_join(data):
    sid = request.sid
    if sid in mm_queue: return
    mm_queue.append(sid)
    for s in mm_queue:
        socketio.emit('matchmaking_count', {'count': len(mm_queue)}, to=s)
    if len(mm_queue) >= 4:
        four    = mm_queue[:4]
        del mm_queue[:4]
        room_id = create_room('4p')
        game    = rooms[room_id]
        game['human_slots'] = [0, 1, 2, 3]
        for i, s in enumerate(four):
            game['players'][i]['is_cpu'] = False
            game['players'][i]['sid']    = s
            game['filled_slots'].append(i)
            players[s] = {'room_id': room_id, 'player_idx': i}
            join_room(room_id, sid=s)
            socketio.emit('joined',
                {'room_id': room_id, 'player_idx': i, 'color': COLORS[i]}, to=s)
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
