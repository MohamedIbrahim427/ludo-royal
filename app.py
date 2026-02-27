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

COLORS = ['red', 'blue', 'green', 'yellow']

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

START_IDX = {'red':0,'blue':13,'green':26,'yellow':39}
ENTRY_BEFORE_HOME = {'red':51,'blue':11,'green':37,'yellow':24}
SAFE_SQUARES = {0,8,13,21,26,34,39,47}

# ─────────────────────────────────────────────

def make_token(idx):
    return {'id':idx,'pos':-1,'stretch':-1,'finished':False}

def make_player(color,is_cpu):
    return {
        'color':color,
        'is_cpu':is_cpu,
        'tokens':[make_token(i) for i in range(4)],
        'finished_count':0,
        'sid':None
    }

# ─────────────────────────────────────────────
# CORRECT LUDO MOVEMENT RULES
# ─────────────────────────────────────────────

def can_move(token,dice,color):
    if token['finished']:
        return False

    # Need 6 to leave home
    if token['pos'] == -1:
        return dice == 6

    # In stretch
    if token['stretch'] >= 0:
        return token['stretch'] + dice <= 5

    # On path
    entry = ENTRY_BEFORE_HOME[color]
    steps_to_entry = (entry - token['pos']) % 52
    if steps_to_entry == 0:
        steps_to_entry = 52

    if dice <= steps_to_entry:
        return True

    steps_into_stretch = dice - steps_to_entry - 1
    return steps_into_stretch <= 5

# ─────────────────────────────────────────────

def apply_move(game,player_idx,token_idx):
    player = game['players'][player_idx]
    token  = player['tokens'][token_idx]
    color  = player['color']
    dice   = game['dice_value']
    events = []

    # Leave home
    if token['pos'] == -1:
        token['pos'] = START_IDX[color]
        token['stretch'] = -1
        cap = check_capture(game,player_idx,token)
        if cap:
            events.append({'type':'capture','by':color,'victim':cap})
        return events

    # In stretch
    if token['stretch'] >= 0:
        token['stretch'] += dice
        if token['stretch'] == 5:
            token['finished'] = True
            player['finished_count'] += 1
            events.append({'type':'home','color':color})
            if player['finished_count'] == 4:
                events.append({'type':'win','color':color})
        return events

    # On path
    entry = ENTRY_BEFORE_HOME[color]
    steps_to_entry = (entry - token['pos']) % 52
    if steps_to_entry == 0:
        steps_to_entry = 52

    if dice <= steps_to_entry:
        token['pos'] = (token['pos'] + dice) % 52
        cap = check_capture(game,player_idx,token)
        if cap:
            events.append({'type':'capture','by':color,'victim':cap})
    else:
        steps_into_stretch = dice - steps_to_entry - 1
        token['pos'] = -2
        token['stretch'] = steps_into_stretch
        if token['stretch'] == 5:
            token['finished'] = True
            player['finished_count'] += 1
            events.append({'type':'home','color':color})
            if player['finished_count'] == 4:
                events.append({'type':'win','color':color})

    return events

# ─────────────────────────────────────────────

def check_capture(game,attacker_idx,token):
    pos = token['pos']
    if pos < 0 or token['stretch'] >= 0:
        return None
    if pos in SAFE_SQUARES:
        return None

    for i,p in enumerate(game['players']):
        if i == attacker_idx: continue
        for t in p['tokens']:
            if t['pos'] == pos and not t['finished']:
                t['pos'] = -1
                t['stretch'] = -1
                return p['color']
    return None

# ─────────────────────────────────────────────
# CPU AI
# ─────────────────────────────────────────────

def cpu_choose_token(game,player_idx):
    player = game['players'][player_idx]
    color  = player['color']
    dice   = game['dice_value']
    movable = [t for t in player['tokens'] if can_move(t,dice,color)]
    if not movable:
        return None
    return movable[0]['id']

# ─────────────────────────────────────────────
# TURN CONTROL
# ─────────────────────────────────────────────

def next_turn(room_id,advance=True):
    game = rooms.get(room_id)
    if not game or game['game_over']: return

    game['rolled'] = False
    if advance:
        game['current_player'] = (game['current_player'] + 1) % 4

    broadcast(room_id)

    cp = game['players'][game['current_player']]
    if cp['is_cpu']:
        start_cpu_turn(room_id)

# ─────────────────────────────────────────────
# CPU TURN
# ─────────────────────────────────────────────

def start_cpu_turn(room_id,delay=1.2):
    def run():
        time.sleep(delay)
        game = rooms.get(room_id)
        if not game or game['game_over']: return

        cp = game['players'][game['current_player']]
        if not cp['is_cpu']: return

        val = random.randint(1,6)
        game['dice_value'] = val
        game['rolled'] = True
        broadcast(room_id)

        movable = [t for t in cp['tokens'] if can_move(t,val,cp['color'])]

        if not movable:
            time.sleep(1)
            next_turn(room_id,advance=True)
            return

        tok_id = cpu_choose_token(game,game['current_player'])
        events = apply_move(game,game['current_player'],tok_id)

        if any(e['type']=='win' for e in events):
            game['game_over'] = True
            game['winner'] = cp['color']
            broadcast(room_id,events)
            return

        broadcast(room_id,events)
        time.sleep(0.6)
        next_turn(room_id,advance=(val!=6))

    threading.Thread(target=run,daemon=True).start()

# ─────────────────────────────────────────────
# ROOM SYSTEM (UNCHANGED)
# ─────────────────────────────────────────────

rooms = {}
players = {}

def create_room(mode):
    room_id = str(uuid.uuid4())[:8].upper()
    game = {
        'room_id':room_id,
        'mode':mode,
        'players':[make_player(COLORS[i],False) for i in range(4)],
        'current_player':0,
        'dice_value':0,
        'rolled':False,
        'game_over':False,
        'winner':None,
        'started':True
    }
    rooms[room_id] = game
    return room_id

def game_to_client(game,events=None):
    data = {
        'room_id':game['room_id'],
        'players':game['players'],
        'current_player':game['current_player'],
        'dice_value':game['dice_value'],
        'rolled':game['rolled'],
        'game_over':game['game_over'],
        'winner':game['winner'],
        'started':game['started']
    }
    if events:
        data['events'] = events
    return data

def broadcast(room_id,events=None):
    game = rooms.get(room_id)
    if game:
        socketio.emit('game_state',game_to_client(game,events),room=room_id)

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    socketio.run(app,host='0.0.0.0',port=5000)
