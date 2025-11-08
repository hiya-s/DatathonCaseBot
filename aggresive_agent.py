import os
from flask import Flask, request, jsonify
from collections import deque
import random

app = Flask(__name__)

PARTICIPANT = "BotLab"
AGENT_NAME = "AggressiveHunter"

BOARD_W, BOARD_H = 20, 18

def normalize(pos):
    return (pos[0] % BOARD_W, pos[1] % BOARD_H)

def get_next(pos, direction):
    delta = {"UP": (0,-1), "DOWN": (0,1), "LEFT": (-1,0), "RIGHT": (1,0)}
    dx, dy = delta[direction]
    return normalize((pos[0]+dx, pos[1]+dy))

def current_dir(trail):
    if len(trail) < 2: return "RIGHT"
    prev, head = trail[-2], trail[-1]
    dx, dy = head[0]-prev[0], head[1]-prev[1]
    if abs(dx)>1: dx = -1 if dx>0 else 1
    if abs(dy)>1: dy = -1 if dy>0 else 1
    if dx==1: return "RIGHT"
    if dx==-1: return "LEFT"
    if dy==1: return "DOWN"
    if dy==-1: return "UP"
    return "RIGHT"

@app.route("/")
def info():
    return jsonify({"participant": PARTICIPANT, "agent_name": AGENT_NAME}), 200

@app.route("/send-state", methods=["POST"])
def receive_state():
    global state
    state = request.get_json() or {}
    return jsonify({"status":"ok"}),200

@app.route("/send-move")
def move():
    s = state
    my_trail = s.get("agent1_trail",[]) if s.get("player_number")==1 else s.get("agent2_trail",[])
    opp_trail = s.get("agent2_trail",[]) if s.get("player_number")==1 else s.get("agent1_trail",[])
    my_pos = tuple(my_trail[-1]) if my_trail else (1,2)
    opp_pos = tuple(opp_trail[-1]) if opp_trail else (17,15)
    occ = {tuple(p) for p in my_trail+opp_trail}
    dirs = ["UP","DOWN","LEFT","RIGHT"]
    cur = current_dir(my_trail)

    # try to move toward opponent
    dx = opp_pos[0]-my_pos[0]
    dy = opp_pos[1]-my_pos[1]
    pref = []
    if abs(dx)>abs(dy):
        pref = ["RIGHT" if dx>0 else "LEFT","DOWN","UP"]
    else:
        pref = ["DOWN" if dy>0 else "UP","RIGHT","LEFT"]

    valid = [d for d in pref+dirs if get_next(my_pos,d) not in occ and d!= {"UP":"DOWN","DOWN":"UP","LEFT":"RIGHT","RIGHT":"LEFT"}[cur]]

    move = valid[0] if valid else cur
    return jsonify({"move": move}),200

@app.route("/end", methods=["POST"])
def end():
    return jsonify({"status":"ok"}),200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5009"))
    app.run(host="0.0.0.0", port=port, debug=False)
