import os
import random
import uuid
import time
import threading
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'ludo-royal-2024')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ─────────────────────────────────────────────
# LUDO CONSTANTS
# ─────────────────────────────────────────────

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
# RULES ROUTE (NEW)
# ─────────────────────────────────────────────

@app.route('/rules')
def rules():
    return jsonify({
        "title": "Ludo Royal - Official Rules",
        "objective": "Move all 4 tokens from base, around the board, into home stretch, and into the center goal.",
        "dice_rules": [
            "Roll one dice per turn (1-6).",
            "Rolling a 6 allows a token to leave base.",
            "Rolling a 6 gives an extra turn."
        ],
        "movement": [
            "Tokens move clockwise.",
            "Move exactly the dice number.",
            "Only one token moves per turn."
        ],
        "base_rule": "You must roll a 6 to bring a token out of base.",
        "capture_rules": [
            "Landing on opponent sends them back to base.",
            "No capture allowed on safe squares."
        ],
        "safe_squares": list(SAFE_SQUARES),
        "home_stretch": [
            "After one full round, tokens enter home stretch.",
            "Exact dice roll required to finish."
        ],
        "winning": "First player to finish all 4 tokens wins.",
        "turn_loss": [
            "If no valid moves are available, turn passes.",
            "If you don't roll 6 while all tokens in base, turn passes."
        ]
    })

# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/health')
def health():
    return {'status': 'ok'}

# ─────────────────────────────────────────────
# START SERVER
# ─────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
