import os
from flask import Flask, request, jsonify
from threading import Lock
from collections import deque

from case_closed_game import Game, Direction, GameResult

# Flask API server setup
app = Flask(__name__)

GLOBAL_GAME = Game()
LAST_POSTED_STATE = {}

game_lock = Lock()
 
PARTICIPANT = "ParticipantX"
AGENT_NAME = "AggressiveSpaceAgent"

# Game constants
BOARD_WIDTH = 20
BOARD_HEIGHT = 18


@app.route("/", methods=["GET"])
def info():
    """Basic health/info endpoint used by the judge to check connectivity."""
    return jsonify({"participant": PARTICIPANT, "agent_name": AGENT_NAME}), 200


def _update_local_game_from_post(data: dict):
    """Update the local GLOBAL_GAME using the JSON posted by the judge."""
    with game_lock:
        LAST_POSTED_STATE.clear()
        LAST_POSTED_STATE.update(data)

        if "board" in data:
            try:
                GLOBAL_GAME.board.grid = data["board"]
            except Exception:
                pass

        if "agent1_trail" in data:
            GLOBAL_GAME.agent1.trail = deque(tuple(p) for p in data["agent1_trail"]) 
        if "agent2_trail" in data:
            GLOBAL_GAME.agent2.trail = deque(tuple(p) for p in data["agent2_trail"]) 
        if "agent1_length" in data:
            GLOBAL_GAME.agent1.length = int(data["agent1_length"])
        if "agent2_length" in data:
            GLOBAL_GAME.agent2.length = int(data["agent2_length"])
        if "agent1_alive" in data:
            GLOBAL_GAME.agent1.alive = bool(data["agent1_alive"])
        if "agent2_alive" in data:
            GLOBAL_GAME.agent2.alive = bool(data["agent2_alive"])
        if "agent1_boosts" in data:
            GLOBAL_GAME.agent1.boosts_remaining = int(data["agent1_boosts"])
        if "agent2_boosts" in data:
            GLOBAL_GAME.agent2.boosts_remaining = int(data["agent2_boosts"])
        if "turn_count" in data:
            GLOBAL_GAME.turns = int(data["turn_count"])


@app.route("/send-state", methods=["POST"])
def receive_state():
    """Judge calls this to push the current game state to the agent server."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "no json body"}), 400
    _update_local_game_from_post(data)
    return jsonify({"status": "state received"}), 200


def normalize_position(pos):
    """Apply torus wrapping to coordinates."""
    return (pos[0] % BOARD_WIDTH, pos[1] % BOARD_HEIGHT)


def get_next_position(pos, direction):
    """Calculate next position given current position and direction."""
    direction_map = {
        "UP": (0, -1),
        "DOWN": (0, 1),
        "LEFT": (-1, 0),
        "RIGHT": (1, 0)
    }
    dx, dy = direction_map[direction]
    return normalize_position((pos[0] + dx, pos[1] + dy))


def is_valid_move(current_dir, new_dir):
    """Check if new direction is valid (not opposite to current)."""
    opposites = {"UP": "DOWN", "DOWN": "UP", "LEFT": "RIGHT", "RIGHT": "LEFT"}
    return new_dir != opposites.get(current_dir)


def flood_fill_fast(start_pos, occupied_cells, max_depth=50):
    """Fast flood fill with depth limit for performance."""
    visited = set()
    queue = deque([(start_pos, 0)])
    visited.add(start_pos)
    count = 0
    
    while queue:
        pos, depth = queue.popleft()
        count += 1
        
        if depth >= max_depth:
            continue
        
        # Check all four directions
        for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
            next_pos = normalize_position((pos[0] + dx, pos[1] + dy))
            
            if next_pos not in visited and next_pos not in occupied_cells:
                visited.add(next_pos)
                queue.append((next_pos, depth + 1))
    
    return count


def predict_opponent_moves(opponent_pos, opponent_trail, occupied_cells):
    """Predict likely opponent positions in next 1-2 moves."""
    if len(opponent_trail) < 2:
        return set()
    
    # Get opponent's current direction
    prev = opponent_trail[-2]
    head = opponent_trail[-1]
    dx = head[0] - prev[0]
    dy = head[1] - prev[1]
    
    # Normalize for torus
    if abs(dx) > 1:
        dx = -1 if dx > 0 else 1
    if abs(dy) > 1:
        dy = -1 if dy > 0 else 1
    
    # Predict next positions (straight, left turn, right turn)
    predictions = set()
    
    # Straight ahead
    next_pos = normalize_position((opponent_pos[0] + dx, opponent_pos[1] + dy))
    if next_pos not in occupied_cells:
        predictions.add(next_pos)
        # Two moves ahead if straight
        next_next = normalize_position((next_pos[0] + dx, next_pos[1] + dy))
        if next_next not in occupied_cells:
            predictions.add(next_next)
    
    # Perpendicular moves (turns)
    if dx != 0:  # Currently moving horizontally
        for new_dy in [-1, 1]:
            turn_pos = normalize_position((opponent_pos[0], opponent_pos[1] + new_dy))
            if turn_pos not in occupied_cells:
                predictions.add(turn_pos)
    else:  # Currently moving vertically
        for new_dx in [-1, 1]:
            turn_pos = normalize_position((opponent_pos[0] + new_dx, opponent_pos[1]))
            if turn_pos not in occupied_cells:
                predictions.add(turn_pos)
    
    return predictions


def evaluate_move_aggressive(my_pos, my_dir, opponent_pos, opponent_trail, direction, 
                             occupied_cells, my_length, opp_length, turn_count, use_boost=False):
    """Aggressive evaluation focusing on space control and survival."""
    if not is_valid_move(my_dir, direction):
        return -100000  # Invalid move
    
    # Simulate the move
    moves = 2 if use_boost else 1
    current_pos = my_pos
    temp_occupied = set(occupied_cells)
    
    for step in range(moves):
        next_pos = get_next_position(current_pos, direction)
        
        # Check if move leads to collision
        if next_pos in temp_occupied:
            return -50000  # Death
        
        temp_occupied.add(next_pos)
        current_pos = next_pos
    
    # Fast flood fill to estimate reachable space
    reachable = flood_fill_fast(current_pos, temp_occupied, max_depth=40)
    
    # Base score heavily weighted on reachable space
    score = reachable * 100
    
    # Predict opponent's likely next positions
    predicted_opp_positions = predict_opponent_moves(opponent_pos, opponent_trail, occupied_cells)
    
    # Penalty for getting too close to predicted opponent positions
    min_pred_dist = float('inf')
    for pred_pos in predicted_opp_positions:
        dist = abs(current_pos[0] - pred_pos[0]) + abs(current_pos[1] - pred_pos[1])
        min_pred_dist = min(min_pred_dist, dist)
    
    # Only penalize if very close
    if min_pred_dist < 3:
        score -= (3 - min_pred_dist) * 50
    
    # Bonus for claiming territory away from opponent
    opp_dist = abs(current_pos[0] - opponent_pos[0]) + abs(current_pos[1] - opponent_pos[1])
    
    # Early game: explore and claim space
    if turn_count < 30:
        score += opp_dist * 5  # Move away from opponent
        
        # Prefer moves toward open space (edges initially)
        edge_dist = min(current_pos[0], BOARD_WIDTH - current_pos[0],
                       current_pos[1], BOARD_HEIGHT - current_pos[1])
        score += edge_dist * 2  # Prefer moving toward edges in early game
    
    # Mid game: maximize territory control
    elif turn_count < 100:
        # Maintain optimal distance (not too close, not too far)
        optimal_dist = 8
        dist_penalty = abs(opp_dist - optimal_dist)
        score -= dist_penalty * 3
        
        # Prefer central positions with more options
        center_x, center_y = BOARD_WIDTH // 2, BOARD_HEIGHT // 2
        dist_to_center = abs(current_pos[0] - center_x) + abs(current_pos[1] - center_y)
        score -= dist_to_center * 2
    
    # Late game: focus on survival and space
    else:
        # If ahead, play safe
        if my_length > opp_length:
            score += reachable * 20  # Heavily favor open space
        else:
            # If behind, take more risks
            score += opp_dist * 10
    
    # Bonus for continuing in same direction (fewer turns = faster expansion)
    direction_value = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}
    if direction == my_dir:
        score += 30  # Bonus for going straight
    
    # Small penalty for turning (wastes time)
    else:
        score -= 10
    
    return score


def get_current_direction(trail):
    """Determine current direction from trail."""
    if len(trail) < 2:
        return "RIGHT"
    
    prev = trail[-2]
    head = trail[-1]
    dx = head[0] - prev[0]
    dy = head[1] - prev[1]
    
    # Normalize for torus wrapping
    if abs(dx) > 1:
        dx = -1 if dx > 0 else 1
    if abs(dy) > 1:
        dy = -1 if dy > 0 else 1
    
    if dx == 1:
        return "RIGHT"
    elif dx == -1:
        return "LEFT"
    elif dy == 1:
        return "DOWN"
    elif dy == -1:
        return "UP"
    
    return "RIGHT"


@app.route("/send-move", methods=["GET"])
def send_move():
    """Judge calls this (GET) to request the agent's move for the current tick."""
    player_number = request.args.get("player_number", default=1, type=int)

    with game_lock:
        state = dict(LAST_POSTED_STATE)   
        my_agent = GLOBAL_GAME.agent1 if player_number == 1 else GLOBAL_GAME.agent2
        opponent_agent = GLOBAL_GAME.agent2 if player_number == 1 else GLOBAL_GAME.agent1
        
        boosts_remaining = my_agent.boosts_remaining
        turn_count = state.get("turn_count", 0)
        
        # Get positions and trails
        my_trail = list(my_agent.trail)
        opponent_trail = list(opponent_agent.trail)
        
        my_length = my_agent.length
        opp_length = opponent_agent.length
        
        if not my_trail:
            return jsonify({"move": "RIGHT"}), 200
        
        my_pos = my_trail[-1]
        opponent_pos = opponent_trail[-1] if opponent_trail else (10, 9)
        
        # Build set of occupied cells
        occupied_cells = set(my_trail + opponent_trail)
        
        # Get current direction
        current_dir = get_current_direction(my_trail)
        
        # Evaluate all possible moves
        directions = ["UP", "DOWN", "LEFT", "RIGHT"]
        best_move = current_dir  # Default to continuing straight
        best_score = -1000000
        use_boost = False
        
        # Evaluate moves without boost
        for direction in directions:
            score = evaluate_move_aggressive(
                my_pos, current_dir, opponent_pos, opponent_trail, direction,
                occupied_cells, my_length, opp_length, turn_count, use_boost=False
            )
            
            if score > best_score:
                best_score = score
                best_move = direction
                use_boost = False
        
        # Consider boost moves
        if boosts_remaining > 0:
            # Use boosts more liberally
            should_use_boost = False
            
            # Use boost in early game to claim territory fast
            if turn_count < 40:
                should_use_boost = True
            
            # Use boost if space is getting tight
            elif best_score < 500:
                should_use_boost = True
            
            # Use boost in mid-game for strategic advantage
            elif 40 <= turn_count <= 120:
                should_use_boost = turn_count % 30 < 10  # Use periodically
            
            # Use remaining boosts in late game
            elif turn_count > 150 and boosts_remaining > 0:
                should_use_boost = True
            
            if should_use_boost:
                boost_best_score = -1000000
                boost_best_move = best_move
                
                for direction in directions:
                    score = evaluate_move_aggressive(
                        my_pos, current_dir, opponent_pos, opponent_trail, direction,
                        occupied_cells, my_length, opp_length, turn_count, use_boost=True
                    )
                    
                    if score > boost_best_score:
                        boost_best_score = score
                        boost_best_move = direction
                
                # Use boost if it's better OR even similar (aggressive)
                if boost_best_score >= best_score - 50:
                    best_move = boost_best_move
                    use_boost = True
        
        # Format move
        move = f"{best_move}:BOOST" if use_boost else best_move

    return jsonify({"move": move}), 200


@app.route("/end", methods=["POST"])
def end_game():
    """Judge notifies agent that the match finished and provides final state."""
    data = request.get_json()
    if data:
        _update_local_game_from_post(data)
    return jsonify({"status": "acknowledged"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5008"))
    app.run(host="0.0.0.0", port=port, debug=True)