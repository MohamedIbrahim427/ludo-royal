import os
import random
import time
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import threading

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'ludo_royal_secret_2024')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ===================== LUDO CONSTANTS =====================

COLORS = ['red', 'blue', 'green', 'yellow']

# Main 52-cell path as [row, col]
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

START_IDX  = {'red': 0,  'blue': 13, 'green': 26, 'yellow': 39}
HOME_ENTRY = {'red': 51, 'blue': 11, 'green': 37, 'yellow': 24}
SAFE_IDX   = [0, 8, 13, 21, 26, 34, 39, 47]

# ===================== GAME ROOMS =====================

rooms = {}   # room_id -> GameRoom
players = {} # sid -> {room_id, color}

class Token:
    def __init__(self, tid):
        self.id = tid
        self.pos = -1      # -1 = home base
        self.stretch = -1  # -1 = not in stretch, 0-5 = position in home stretch
        self.finished = False

    def to_dict(self):
        return {'id': self.id, 'pos': self.pos, 'stretch': self.stretch, 'finished': self.finished}


class Player:
    def __init__(self, color, is_cpu=False, name=None, sid=None):
        self.color = color
        self.is_cpu = is_cpu
        self.name = name or (f'CPU {color.capitalize()}' if is_cpu else color.capitalize())
        self.sid = sid
        self.tokens = [Token(i) for i in range(4)]
        self.finished_count = 0

    def to_dict(self):
        return {
            'color': self.color,
            'is_cpu': self.is_cpu,
            'name': self.name,
            'finished_count': self.finished_count,
            'tokens': [t.to_dict() for t in self.tokens],
        }


class GameRoom:
    def __init__(self, room_id, mode):
        self.room_id = room_id
        self.mode = mode       # '4p','1v3','2v2','3v1'
        self.players = []
        self.current = 0
        self.dice_value = 0
        self.rolled = False
        self.game_over = False
        self.winner = None
        self.status = 'waiting'   # waiting / playing / finished
        self.host_sid = None
        self._setup_players(mode)

    def _setup_players(self, mode):
        cpu_map = {
            '4p':  [False, False, False, False],
            '1v3': [False, True,  True,  True ],
            '2v2': [False, False, True,  True ],
            '3v1': [False, False, False, True ],
        }
        flags = cpu_map.get(mode, [False]*4)
        for i, color in enumerate(COLORS):
            self.players.append(Player(color, is_cpu=flags[i]))

    def human_slots(self):
        return [p for p in self.players if not p.is_cpu]

    def assign_human(self, sid, name):
        for p in self.players:
            if not p.is_cpu and p.sid is None:
                p.sid = sid
                p.name = name
                return p
        return None

    def all_humans_joined(self):
        return all(p.sid is not None for p in self.players if not p.is_cpu)

    def state(self):
        return {
            'room_id': self.room_id,
            'mode': self.mode,
            'players': [p.to_dict() for p in self.players],
            'current': self.current,
            'dice_value': self.dice_value,
            'rolled': self.rolled,
            'game_over': self.game_over,
            'winner': self.winner,
            'status': self.status,
        }

    # ---- Move logic ----
    def can_move(self, token, dice, color):
        if token.finished:
            return False
        if token.pos == -1:
            return dice == 6
        if token.stretch >= 0:
            return token.stretch + dice <= 5
        # on path
        dist_to_entry = (HOME_ENTRY[color] - token.pos) % 52
        if dist_to_entry == 0:
            dist_to_entry = 52
        if dice <= dist_to_entry:
            return True
        extra = dice - dist_to_entry - 1
        return extra <= 5

    def movable_tokens(self, player_idx, dice):
        p = self.players[player_idx]
        return [t for t in p.tokens if self.can_move(t, dice, p.color)]

    def move_token(self, player_idx, token_id):
        p = self.players[player_idx]
        token = next((t for t in p.tokens if t.id == token_id), None)
        if token is None or not self.can_move(token, self.dice_value, p.color):
            return False, None

        dice = self.dice_value
        color = p.color
        events = []

        if token.pos == -1:
            token.pos = START_IDX[color]
            captured = self._check_capture(token, p)
            if captured:
                events.append({'type':'capture','by':color,'victim':captured})
        elif token.stretch >= 0:
            token.stretch += dice
            if token.stretch >= 6:
                token.stretch = 6
                token.finished = True
                p.finished_count += 1
                events.append({'type':'home','color':color,'token':token_id})
        else:
            dist_to_entry = (HOME_ENTRY[color] - token.pos) % 52
            if dist_to_entry == 0:
                dist_to_entry = 52
            if dice <= dist_to_entry:
                token.pos = (token.pos + dice) % 52
                captured = self._check_capture(token, p)
                if captured:
                    events.append({'type':'capture','by':color,'victim':captured})
            else:
                extra = dice - dist_to_entry - 1
                token.pos = HOME_ENTRY[color]
                token.stretch = extra
                if token.stretch >= 6:
                    token.stretch = 6
                    token.finished = True
                    p.finished_count += 1
                    events.append({'type':'home','color':color,'token':token_id})

        # Check win
        if p.finished_count >= 4:
            self.game_over = True
            self.winner = color
            self.status = 'finished'
            events.append({'type':'win','color':color,'name':p.name})

        self.rolled = False
        return True, events

    def _check_capture(self, moved_token, moved_player):
        pos = moved_token.pos
        if pos in SAFE_IDX:
            return None
        for p in self.players:
            if p is moved_player:
                continue
            for t in p.tokens:
                if t.pos == pos and t.stretch < 0 and not t.finished:
                    t.pos = -1
                    t.stretch = -1
                    return p.color
        return None

    def cpu_choose_token(self, dice):
        p = self.players[self.current]
        movable = self.movable_tokens(self.current, dice)
        if not movable:
            return None
        color = p.color

        # Priority: capture > exit home (dice=6) > most advanced
        for t in movable:
            if t.pos != -1 and t.stretch < 0:
                dist = (HOME_ENTRY[color] - t.pos) % 52
                if dist == 0: dist = 52
                if dice <= dist:
                    np_ = (t.pos + dice) % 52
                    if np_ not in SAFE_IDX:
                        for op in self.players:
                            if op is not p:
                                for ot in op.tokens:
                                    if ot.pos == np_ and ot.stretch < 0 and not ot.finished:
                                        return t.id

        if dice == 6:
            for t in movable:
                if t.pos == -1:
                    return t.id

        best = max(movable, key=lambda t: t.pos if t.pos >= 0 else -999)
        return best.id


# ===================== SOCKET EVENTS =====================

def broadcast_state(room_id):
    room = rooms.get(room_id)
    if room:
        socketio.emit('game_state', room.state(), to=room_id)


def next_turn(room_id, gave_bonus):
    room = rooms.get(room_id)
    if not room or room.game_over:
        return
    if not gave_bonus:
        room.current = (room.current + 1) % 4
    room.rolled = False
    broadcast_state(room_id)
    cur = room.players[room.current]
    if cur.is_cpu:
        threading.Thread(target=cpu_turn, args=(room_id,), daemon=True).start()


def cpu_turn(room_id):
    time.sleep(0.9)
    room = rooms.get(room_id)
    if not room or room.game_over:
        return

    dice = random.randint(1, 6)
    room.dice_value = dice
    room.rolled = True
    socketio.emit('dice_rolled', {'value': dice, 'player': room.current, 'is_cpu': True}, to=room_id)

    time.sleep(0.7)
    movable = room.movable_tokens(room.current, dice)
    if not movable:
        socketio.emit('no_moves', {'player': room.current}, to=room_id)
        time.sleep(0.8)
        next_turn(room_id, dice == 6)
        return

    token_id = room.cpu_choose_token(dice)
    ok, events = room.move_token(room.current, token_id)
    if ok:
        for ev in events:
            socketio.emit('game_event', ev, to=room_id)
        broadcast_state(room_id)
        if room.game_over:
            return
        time.sleep(0.3)
        next_turn(room_id, dice == 6)


@app.route('/')
def index():
    return render_template('index.html')


@socketio.on('create_room')
def on_create_room(data):
    mode = data.get('mode', '1v3')
    name = data.get('name', 'Player')
    room_id = str(random.randint(10000, 99999))
    while room_id in rooms:
        room_id = str(random.randint(10000, 99999))

    room = GameRoom(room_id, mode)
    rooms[room_id] = room
    room.host_sid = request.sid

    player = room.assign_human(request.sid, name)
    players[request.sid] = {'room_id': room_id, 'color': player.color if player else None}

    join_room(room_id)
    emit('room_created', {'room_id': room_id, 'color': player.color if player else None, 'mode': mode})

    if room.all_humans_joined():
        room.status = 'playing'
        broadcast_state(room_id)
        cur = room.players[room.current]
        if cur.is_cpu:
            threading.Thread(target=cpu_turn, args=(room_id,), daemon=True).start()


@socketio.on('join_room_request')
def on_join_room(data):
    room_id = str(data.get('room_id', ''))
    name = data.get('name', 'Player')

    if room_id not in rooms:
        emit('error', {'msg': 'Room not found!'})
        return

    room = rooms[room_id]
    if room.status == 'finished':
        emit('error', {'msg': 'Game already finished!'})
        return

    player = room.assign_human(request.sid, name)
    if player is None:
        emit('error', {'msg': 'Room is full!'})
        return

    players[request.sid] = {'room_id': room_id, 'color': player.color}
    join_room(room_id)

    emit('room_joined', {'room_id': room_id, 'color': player.color, 'mode': room.mode})
    socketio.emit('player_joined', {'name': name, 'color': player.color}, to=room_id)

    if room.all_humans_joined():
        room.status = 'playing'
        broadcast_state(room_id)
        cur = room.players[room.current]
        if cur.is_cpu:
            threading.Thread(target=cpu_turn, args=(room_id,), daemon=True).start()
    else:
        broadcast_state(room_id)


@socketio.on('roll_dice')
def on_roll_dice():
    info = players.get(request.sid)
    if not info:
        return

    room = rooms.get(info['room_id'])
    if not room or room.game_over or room.rolled:
        return

    cur_player = room.players[room.current]
    if cur_player.sid != request.sid or cur_player.is_cpu:
        return

    dice = random.randint(1, 6)
    room.dice_value = dice
    room.rolled = True

    socketio.emit('dice_rolled', {'value': dice, 'player': room.current, 'is_cpu': False}, to=info['room_id'])

    movable = room.movable_tokens(room.current, dice)
    if not movable:
        socketio.emit('no_moves', {'player': room.current}, to=info['room_id'])
        threading.Timer(1.2, lambda: next_turn(info['room_id'], dice == 6)).start()
    else:
        broadcast_state(info['room_id'])


@socketio.on('move_token')
def on_move_token(data):
    token_id = data.get('token_id')
    info = players.get(request.sid)
    if not info:
        return

    room = rooms.get(info['room_id'])
    if not room or room.game_over or not room.rolled:
        return

    cur_player = room.players[room.current]
    if cur_player.sid != request.sid:
        return

    ok, events = room.move_token(room.current, token_id)
    if ok:
        for ev in events:
            socketio.emit('game_event', ev, to=info['room_id'])
        broadcast_state(info['room_id'])
        if not room.game_over:
            bonus = room.dice_value == 6
            threading.Timer(0.3, lambda: next_turn(info['room_id'], bonus)).start()


@socketio.on('disconnect')
def on_disconnect():
    info = players.pop(request.sid, None)
    if info:
        room = rooms.get(info['room_id'])
        if room:
            for p in room.players:
                if p.sid == request.sid:
                    p.sid = None
                    p.name = f'{p.color.capitalize()} (left)'
                    break
            socketio.emit('player_left', {'color': info['color']}, to=info['room_id'])


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
