# 🎲 LUDO ROYAL – Multiplayer Server

A real-time multiplayer Ludo game built with Python (Flask + Socket.IO) and vanilla HTML/JS.

## Features
- ♛ Real-time multiplayer via WebSockets
- 🎮 4 game modes: 4P, 1v3 CPU, 2v2 CPU, 3v1 CPU
- 🤖 CPU AI with capture strategy
- 🔑 Room code system – share code to invite friends
- 📱 Mobile responsive

## Deploy on Railway

### Method 1 – GitHub (Recommended)
1. Push this folder to a GitHub repo
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Select your repo
4. Railway auto-detects Python and deploys!
5. Add environment variable: `SECRET_KEY` = any random string

### Method 2 – Railway CLI
```bash
npm install -g @railway/cli
railway login
cd ludo-server
railway init
railway up
```

### Environment Variables
| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | Port (set by Railway automatically) | 5000 |
| `SECRET_KEY` | Flask secret key | ludo-royal-secret-2024 |

## Local Development
```bash
pip install -r requirements.txt
python app.py
# Open http://localhost:5000
```

## How to Play
1. **Host** clicks a game mode → gets a room code
2. **Friends** enter the room code and click JOIN
3. Once all human players join, the game starts automatically
4. Roll dice on your turn, click a highlighted token to move
5. First player to get all 4 tokens home wins!

## File Structure
```
ludo-server/
├── app.py              # Flask + SocketIO server
├── templates/
│   └── index.html      # Full game frontend
├── requirements.txt    # Python dependencies
├── Procfile            # For Railway/Heroku
├── railway.toml        # Railway config
└── README.md
```
