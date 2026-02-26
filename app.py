import os
import random
import uuid
import time
import threading
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'ludo-royal-secret-2024')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ─────────────────────────────────────────────
# LUDO GAME LOGIC
# ─────────────────────────────────────────────

COLORS = ['red', 'blue', 'green', 'yellow']

# Standard Ludo 52-cell outer path [row, col]
PATH = [
    [6,1],[6,2],[6,3],[6,4],[6,5],
    [5,6],[4,6],[3,6],[2,6],[1,6],[0,6],
    [0,7],
    [0,8],[1,8],[2,8],[3,8],[4,8],[5,8],
    [6,9],[6,10],[6,11],[6,12],[6,13],[6,14],
    [7,14],
    [8,14],[8,13],[8,12],[8,11],[8,10],[8,9],
    [9,8],[10,8],[11,8],[12,8],[13,8],[14,8],
    [14,7],
    [14,6],[13,6],[12,6],[11,6],[10,6],[9,6],
    [8,5],[8,4],[8,3],[8,2],[8,1],[8,0],
    [7,0],
    [6,0],
]

HOME_STRETCH = {
    'red':    [[7,1],[7,2],[7,3],[7,4],[7,5],[7,6]],
    'blue':   [[1,7],[2,7],[3,7],[4,7],[5,7],[6,7]],
    'green':  [[13,7],[12,7],[11,7],[10,7],[9,7],[8,7]],
    'yellow': [[7,13],[7,12],[7,11],[7,10],[7,9],[7,8]],
}

HOME_POS = {
    'red':    [[1,1],[1,2],[2,1],[2,2]],
    'blue':   [[1,12],[1,13],[2,12],[2,13]],
    'green':  [[12,1],[12,2],[13,1],[13,2]],
    'yellow': [[12,12],[12,13],[13,12],[13,13]],
}

START_IDX  = {'red': 0, 'blue': 13, 'green': 26, 'yellow': 39}
HOME_ENTRY = {'red':51, 'blue':11, 'green':37, 'yellow':24}
SAFE_IDX   = {0, 8, 13, 21, 26, 34, 39, 47}


def make_token(idx):
    return {'id': idx, 'pos': -1, 'stretch': -1, 'finished': False}


def make_player(color, is_cpu, name):
    return {
        'color': color,
        'is_cpu': is_cpu,
        'name': name,
        'tokens': [make_token(i) for i in range(4)],
        'finished_count': 0,
        'sid': None,
    }


def can_move(token, dice, color):
    if token['finished']:
        return False
    if token['pos'] == -1:
        return dice == 6
    if token['stretch'] >= 0:
        return token['stretch'] + dice <= 5
    entry = HOME_ENTRY[color]
    dist = (entry - token['pos'] + 52) % 52
    if dist == 0:
        dist = 52
    if dice <= dist:
        return True
    into = dice - dist - 1
    return into <= 5


def apply_move(game, player_idx, token_idx):
    player = game['players'][player_idx]
    token  = player['tokens'][token_idx]
    color  = player['color']
    dice   = game['dice_value']
    events = []

    if token['pos'] == -1:
        token['pos'] = START_IDX[color]
        cap = check_capture(game, player_idx, token)
        if cap:
            events.append({'type': 'capture', 'by': color, 'victim': cap})
    elif token['stretch'] >= 0:
        token['stretch'] += dice
        if token['stretch'] >= 6:
            token['stretch'] = 6
            token['finished'] = True
            player['finished_count'] += 1
            events.append({'type': 'home', 'color': color})
            if player['finished_count'] == 4:
                events.append({'type': 'win', 'color': color})
    else:
        entry = HOME_ENTRY[color]
        dist  = (entry - token['pos'] + 52) % 52
        if dist == 0:
            dist = 52
        if dice <= dist:
            token['pos'] = (token['pos'] + dice) % 52
            cap = check_capture(game, player_idx, token)
            if cap:
                events.append({'type': 'capture', 'by': color, 'victim': cap})
        else:
            token['pos'] = entry
            into = dice - dist - 1
            token['stretch'] = into
            if token['stretch'] >= 6:
                token['stretch'] = 6
                token['finished'] = True
                player['finished_count'] += 1
                events.append({'type': 'home', 'color': color})
                if player['finished_count'] == 4:
                    events.append({'type': 'win', 'color': color})

    return events


def check_capture(game, attacker_idx, token):
    pos = token['pos']
    if pos == -1 or token['stretch'] >= 0:
        return None
    if pos in SAFE_IDX:
        return None
    for i, p in enumerate(game['players']):
        if i == attacker_idx:
            continue
        for t in p['tokens']:
            if t['pos'] == pos and t['stretch'] < 0 and not t['finished']:
                t['pos'] = -1
                t['stretch'] = -1
                return p['color']
    return None


def cpu_choose_token(game, player_idx):
    player = game['players'][player_idx]
    color  = player['color']
    dice   = game['dice_value']
    movable = [t for t in player['tokens'] if can_move(t, dice, color)]
    if not movable:
        return None

    # Priority: capture > exit home > most advanced
    for t in movable:
        if t['pos'] != -1 and t['stretch'] < 0:
            entry = HOME_ENTRY[color]
            dist  = (entry - t['pos'] + 52) % 52
            if dist == 0: dist = 52
            if dice <= dist:
                np = (t['pos'] + dice) % 52
                if np not in SAFE_IDX:
                    for p2 in game['players']:
                        if p2['color'] == color: continue
                        if any(ot['pos'] == np and ot['stretch'] < 0 for ot in p2['tokens']):
                            return t['id']

    for t in movable:
        if t['pos'] == -1 and dice == 6:
            return t['id']

    best = max(movable, key=lambda t: t['stretch'] if t['stretch'] >= 0 else (t['pos'] if t['pos'] >= 0 else -99))
    return best['id']


# ─────────────────────────────────────────────
# ROOMS / LOBBY
# ─────────────────────────────────────────────

rooms  = {}   # room_id -> game state
players = {}  # sid -> {room_id, player_idx}


def create_room(mode):
    room_id = str(uuid.uuid4())[:8].upper()
    cpu_map = {
        '4p':  [False, False, False, False],
        '1v3': [False, True,  True,  True ],
        '2v2': [False, False, True,  True ],
        '3v1': [False, False, False, True ],
    }
    is_cpus = cpu_map.get(mode, [False]*4)
    game = {
        'room_id': room_id,
        'mode': mode,
        'players': [
            make_player(COLORS[i], is_cpus[i], f"{COLORS[i].capitalize()} {'🤖' if is_cpus[i] else '👤'}")
            for i in range(4)
        ],
        'current_player': 0,
        'dice_value': 0,
        'rolled': False,
        'game_over': False,
        'winner': None,
        'human_slots': [i for i, c in enumerate(is_cpus) if not c],
        'filled_slots': [],
        'started': False,
    }
    rooms[room_id] = game
    return room_id


def get_state_for_client(game):
    return {
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


def broadcast_state(room_id, event_list=None):
    game = rooms.get(room_id)
    if not game:
        return
    payload = get_state_for_client(game)
    if event_list:
        payload['events'] = event_list
    socketio.emit('game_state', payload, room=room_id)


def schedule_cpu_turn(room_id, delay=1.2):
    def run():
        time.sleep(delay)
        game = rooms.get(room_id)
        if not game or game['game_over'] or game['rolled']:
            return
        cp = game['players'][game['current_player']]
        if not cp['is_cpu']:
            return
        val = random.randint(1, 6)
        game['dice_value'] = val
        game['rolled']     = True
        socketio.emit('game_state', get_state_for_client(game), room=room_id)

        movable = [t for t in cp['tokens'] if can_move(t, val, cp['color'])]
        if not movable:
            socketio.emit('notification', {'msg': f"{cp['color'].upper()} has no moves!"}, room=room_id)
            time.sleep(1.0)
            advance_turn(room_id, val != 6)
            return

        time.sleep(0.7)
        tok_id = cpu_choose_token(game, game['current_player'])
        events = apply_move(game, game['current_player'], tok_id)

        win = next((e for e in events if e['type'] == 'win'), None)
        if win:
            game['game_over'] = True
            game['winner']    = win['color']
            broadcast_state(room_id, events)
            return

        broadcast_state(room_id, events)
        time.sleep(0.5)
        advance_turn(room_id, val != 6)

    t = threading.Thread(target=run, daemon=True)
    t.start()


def advance_turn(room_id, next_player=True):
    game = rooms.get(room_id)
    if not game or game['game_over']:
        return
    game['rolled'] = False
    if next_player:
        game['current_player'] = (game['current_player'] + 1) % 4
    cp = game['players'][game['current_player']]
    broadcast_state(room_id)
    if cp['is_cpu']:
        schedule_cpu_turn(room_id)


# ─────────────────────────────────────────────
# SOCKET EVENTS
# ─────────────────────────────────────────────

@socketio.on('connect')
def on_connect():
    print(f"[+] Connected: {request.sid}")


@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    info = players.pop(sid, None)
    if info:
        room_id = info['room_id']
        game    = rooms.get(room_id)
        if game:
            pidx = info['player_idx']
            game['players'][pidx]['sid'] = None
            # Mark as CPU so game continues
            game['players'][pidx]['is_cpu'] = True
            game['players'][pidx]['name'] = game['players'][pidx]['name'].replace('👤','🤖 (left)')
            broadcast_state(room_id)
    print(f"[-] Disconnected: {sid}")


@socketio.on('create_room')
def on_create_room(data):
    mode    = data.get('mode', '1v3')
    room_id = create_room(mode)
    game    = rooms[room_id]

    # Assign first human slot to creator
    slot = game['human_slots'][0]
    game['players'][slot]['sid'] = request.sid
    game['filled_slots'].append(slot)
    players[request.sid] = {'room_id': room_id, 'player_idx': slot}

    join_room(room_id)
    emit('joined', {'room_id': room_id, 'player_idx': slot, 'color': COLORS[slot]})

    # If no more humans needed, start immediately
    if len(game['human_slots']) == 1:
        game['started'] = True
        broadcast_state(room_id)
        cp = game['players'][game['current_player']]
        if cp['is_cpu']:
            schedule_cpu_turn(room_id)
    else:
        broadcast_state(room_id)


@socketio.on('join_room')
def on_join_room(data):
    room_id = data.get('room_id', '').upper().strip()
    game    = rooms.get(room_id)
    if not game:
        emit('error', {'msg': 'Room not found!'})
        return
    if game['started']:
        emit('error', {'msg': 'Game already started!'})
        return

    # Find next open human slot
    open_slots = [s for s in game['human_slots'] if s not in game['filled_slots']]
    if not open_slots:
        emit('error', {'msg': 'Room is full!'})
        return

    slot = open_slots[0]
    game['players'][slot]['sid'] = request.sid
    game['filled_slots'].append(slot)
    players[request.sid] = {'room_id': room_id, 'player_idx': slot}

    join_room(room_id)
    emit('joined', {'room_id': room_id, 'player_idx': slot, 'color': COLORS[slot]})

    # Start if all humans joined
    if set(game['human_slots']) == set(game['filled_slots']):
        game['started'] = True

    broadcast_state(room_id)
    if game['started']:
        cp = game['players'][game['current_player']]
        if cp['is_cpu']:
            schedule_cpu_turn(room_id)


@socketio.on('roll_dice')
def on_roll_dice(data):
    sid     = request.sid
    info    = players.get(sid)
    if not info:
        return
    room_id = info['room_id']
    pidx    = info['player_idx']
    game    = rooms.get(room_id)
    if not game or game['game_over'] or game['rolled']:
        return
    if game['current_player'] != pidx:
        emit('error', {'msg': "Not your turn!"})
        return

    val = random.randint(1, 6)
    game['dice_value'] = val
    game['rolled']     = True

    cp      = game['players'][pidx]
    movable = [t for t in cp['tokens'] if can_move(t, val, cp['color'])]

    broadcast_state(room_id)

    if not movable:
        socketio.emit('notification', {'msg': f"No moves for {cp['color'].upper()}!"}, room=room_id)
        time.sleep(0.8)
        advance_turn(room_id, val != 6)


@socketio.on('move_token')
def on_move_token(data):
    sid      = request.sid
    info     = players.get(sid)
    if not info:
        return
    room_id  = info['room_id']
    pidx     = info['player_idx']
    token_id = data.get('token_id')
    game     = rooms.get(room_id)

    if not game or game['game_over'] or not game['rolled']:
        return
    if game['current_player'] != pidx:
        return

    cp = game['players'][pidx]
    if token_id is None or token_id < 0 or token_id >= 4:
        return
    token = cp['tokens'][token_id]
    if not can_move(token, game['dice_value'], cp['color']):
        emit('error', {'msg': 'Cannot move that token!'})
        return

    events = apply_move(game, pidx, token_id)

    win = next((e for e in events if e['type'] == 'win'), None)
    if win:
        game['game_over'] = True
        game['winner']    = win['color']
        broadcast_state(room_id, events)
        return

    broadcast_state(room_id, events)
    advance_turn(room_id, game['dice_value'] != 6)


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
