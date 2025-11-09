import os
from flask import Flask, request, jsonify
from threading import Lock
from collections import deque
import time

from case_closed_game import Game, Direction, GameResult

# Flask API server setup
app = Flask(__name__)

GLOBAL_GAME = Game()
LAST_POSTED_STATE = {}

game_lock = Lock()
 
PARTICIPANT = "ParticipantX"
AGENT_NAME = "OptimalSurvivalAgent"

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


def get_opposite_direction(direction):
    """Get opposite direction."""
    opposites = {"UP": "DOWN", "DOWN": "UP", "LEFT": "RIGHT", "RIGHT": "LEFT"}
    return opposites.get(direction, "UP")


def flood_fill_with_territories(my_pos, opp_pos, occupied_cells):
    """
    Flood fill from both positions to determine territory control.
    Returns (my_territory, opp_territory, neutral_territory)
    """
    my_visited = {my_pos}
    opp_visited = {opp_pos}
    
    my_queue = deque([my_pos])
    opp_queue = deque([opp_pos])
    
    # BFS from both positions simultaneously
    while my_queue or opp_queue:
        # Expand my territory
        if my_queue:
            for _ in range(len(my_queue)):
                pos = my_queue.popleft()
                
                for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
                    next_pos = normalize_position((pos[0] + dx, pos[1] + dy))
                    
                    if next_pos not in occupied_cells and next_pos not in my_visited and next_pos not in opp_visited:
                        my_visited.add(next_pos)
                        my_queue.append(next_pos)
        
        # Expand opponent territory
        if opp_queue:
            for _ in range(len(opp_queue)):
                pos = opp_queue.popleft()
                
                for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
                    next_pos = normalize_position((pos[0] + dx, pos[1] + dy))
                    
                    if next_pos not in occupied_cells and next_pos not in my_visited and next_pos not in opp_visited:
                        opp_visited.add(next_pos)
                        opp_queue.append(next_pos)
    
    return len(my_visited), len(opp_visited)


def minimax_evaluate(my_pos, opp_pos, occupied_cells, depth, is_my_turn, my_dir, opp_dir, alpha, beta, start_time, time_limit=0.5):
    """
    Minimax with alpha-beta pruning to look ahead several moves.
    Returns the score of the position.
    """
    # Time cutoff
    if time.time() - start_time > time_limit:
        my_territory, opp_territory = flood_fill_with_territories(my_pos, opp_pos, occupied_cells)
        return my_territory - opp_territory
    
    # Base case: depth limit reached
    if depth == 0:
        my_territory, opp_territory = flood_fill_with_territories(my_pos, opp_pos, occupied_cells)
        return my_territory - opp_territory
    
    directions = ["UP", "DOWN", "LEFT", "RIGHT"]
    
    if is_my_turn:
        max_eval = -999999
        
        for direction in directions:
            if not is_valid_move(my_dir, direction):
                continue
            
            next_pos = get_next_position(my_pos, direction)
            
            # Check if move causes collision
            if next_pos in occupied_cells:
                continue  # Skip this move
            
            # Simulate move
            new_occupied = occupied_cells | {next_pos}
            
            eval_score = minimax_evaluate(next_pos, opp_pos, new_occupied, depth - 1, False, 
                                         direction, opp_dir, alpha, beta, start_time, time_limit)
            
            max_eval = max(max_eval, eval_score)
            alpha = max(alpha, eval_score)
            
            if beta <= alpha:
                break  # Beta cutoff
        
        return max_eval if max_eval > -999999 else -999999
    
    else:  # Opponent's turn
        min_eval = 999999
        
        for direction in directions:
            if not is_valid_move(opp_dir, direction):
                continue
            
            next_pos = get_next_position(opp_pos, direction)
            
            # Check if move causes collision
            if next_pos in occupied_cells:
                continue
            
            # Simulate move
            new_occupied = occupied_cells | {next_pos}
            
            eval_score = minimax_evaluate(my_pos, next_pos, new_occupied, depth - 1, True,
                                         my_dir, direction, alpha, beta, start_time, time_limit)
            
            min_eval = min(min_eval, eval_score)
            beta = min(beta, eval_score)
            
            if beta <= alpha:
                break  # Alpha cutoff
        
        return min_eval if min_eval < 999999 else 999999


def space_filling_heuristic(pos, occupied_cells, direction, current_dir):
    """
    Heuristic to encourage space-filling patterns (spirals, zigzags).
    """
    score = 0
    
    # Strongly prefer avoiding occupied cells
    next_pos = get_next_position(pos, direction)
    if next_pos in occupied_cells:
        return -100000
    
    # Check 2 steps ahead
    next_next_pos = get_next_position(next_pos, direction)
    if next_next_pos in occupied_cells:
        score -= 5000
    
    # Count free neighbors around next position (more free = better)
    free_neighbors = 0
    for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
        neighbor = normalize_position((next_pos[0] + dx, next_pos[1] + dy))
        if neighbor not in occupied_cells:
            free_neighbors += 1
    
    score += free_neighbors * 100
    
    # Prefer making right-angle turns periodically for space-filling
    # This creates a zigzag/spiral pattern
    if direction != current_dir and is_valid_move(current_dir, direction):
        score += 50  # Bonus for turning
    
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


def get_safe_moves(pos, current_dir, occupied_cells):
    """Get all moves that don't immediately cause collision."""
    safe_moves = []
    directions = ["UP", "DOWN", "LEFT", "RIGHT"]
    
    for direction in directions:
        if not is_valid_move(current_dir, direction):
            continue
        
        next_pos = get_next_position(pos, direction)
        if next_pos not in occupied_cells:
            safe_moves.append(direction)
    
    return safe_moves


def evaluate_with_lookahead(my_pos, opp_pos, occupied_cells, my_dir, opp_dir, direction, start_time):
    """Evaluate a move using minimax with territory control."""
    if not is_valid_move(my_dir, direction):
        return -100000
    
    next_pos = get_next_position(my_pos, direction)
    
    if next_pos in occupied_cells:
        return -100000
    
    # Simulate the move
    new_occupied = occupied_cells | {next_pos}
    
    # Use minimax to look ahead
    depth = 4  # Look 4 moves ahead
    score = minimax_evaluate(next_pos, opp_pos, new_occupied, depth, False, 
                            direction, opp_dir, -999999, 999999, start_time, time_limit=0.8)
    
    # Add space-filling heuristic
    space_score = space_filling_heuristic(my_pos, occupied_cells, direction, my_dir)
    
    return score + space_score


@app.route("/send-move", methods=["GET"])
def send_move():
    """Deep Q-learning inspired agent with advanced space evaluation."""
    player_number = request.args.get("player_number", default=1, type=int)

    with game_lock:
        state = dict(LAST_POSTED_STATE)
        my_agent = GLOBAL_GAME.agent1 if player_number == 1 else GLOBAL_GAME.agent2
        opp_agent = GLOBAL_GAME.agent2 if player_number == 1 else GLOBAL_GAME.agent1

        my_trail = list(my_agent.trail)
        opp_trail = list(opp_agent.trail)
        if not my_trail:
            return jsonify({"move": "RIGHT"}), 200

        my_pos = my_trail[-1]
        opp_pos = opp_trail[-1] if opp_trail else (10, 9)
        my_dir = get_current_direction(my_trail)
        occupied = set(my_trail + opp_trail)

        def deep_flood_score(pos, depth=3):
            """Multi-level flood fill that looks ahead several moves."""
            if depth == 0:
                return 0
            
            score = 0
            visited = {pos}
            queue = deque([(pos, 1.0)])  # (position, weight)
            
            while queue:
                current_pos, weight = queue.popleft()
                score += weight  # Add weighted score for this position
                
                if depth > 1:  # Only explore further if we have depth remaining
                    for dx, dy in [(0,1), (0,-1), (1,0), (-1,0)]:
                        nx, ny = (current_pos[0] + dx) % BOARD_WIDTH, (current_pos[1] + dy) % BOARD_HEIGHT
                        next_pos = (nx, ny)
                        
                        if next_pos not in visited and next_pos not in occupied:
                            visited.add(next_pos)
                            # Decay weight with distance to prioritize closer spaces
                            next_weight = weight * 0.8
                            queue.append((next_pos, next_weight))
            
            return score

        def evaluate_move(pos, direction, opp_pos):
            """Comprehensive move evaluation combining multiple factors."""
            next_pos = get_next_position(pos, direction)
            if next_pos in occupied:
                return float('-inf')
            
            # Base space score from deep flood fill
            space_score = deep_flood_score(next_pos)
            
            # Distance from opponent's trail (prefer staying away)
            opp_distance = min(
                abs(next_pos[0] - tx) + abs(next_pos[1] - ty)
                for tx, ty in opp_trail
            ) if opp_trail else BOARD_WIDTH
            distance_score = min(opp_distance * 10, 100)  # Cap the distance score
            
            # Look ahead for future moves
            future_moves = 0
            future_pos = next_pos
            future_visited = {pos, next_pos}
            
            for _ in range(3):  # Look 3 moves ahead
                valid_futures = [
                    get_next_position(future_pos, d)
                    for d in ["UP", "DOWN", "LEFT", "RIGHT"]
                    if is_valid_move(direction, d)
                ]
                valid_futures = [p for p in valid_futures if p not in occupied and p not in future_visited]
                if not valid_futures:
                    break
                future_moves += len(valid_futures)
                future_visited.update(valid_futures)
            
            # Combine scores with weights
            total_score = (
                space_score * 2.0 +          # Prioritize available space
                distance_score * 0.5 +       # Moderate weight for opponent distance
                future_moves * 15.0          # Good weight for future move options
            )
            
            return total_score

        # Generate and evaluate all valid moves
        dirs = ["UP", "DOWN", "LEFT", "RIGHT"]
        moves_with_scores = []
        
        for direction in dirs:
            if not is_valid_move(my_dir, direction):
                continue
            
            score = evaluate_move(my_pos, direction, opp_pos)
            if score > float('-inf'):
                moves_with_scores.append((direction, score))
        
        if not moves_with_scores:
            return jsonify({"move": my_dir}), 200
        
        # Select best move
        best_move, best_score = max(moves_with_scores, key=lambda x: x[1])
        
        # Smart boost usage
        use_boost = False
        if my_agent.boosts_remaining > 0:
            current_space = deep_flood_score(my_pos)
            next_pos = get_next_position(my_pos, best_move)
            next_space = deep_flood_score(next_pos)
            
            # Use boost if:
            # 1. We're getting boxed in (limited space)
            # 2. Moving to a significantly better position
            # 3. Early/mid game and good opportunity
            if (current_space < 50 or                    # Boxing in
                next_space > current_space * 1.5 or      # Much better position
                (state.get("turn_count", 0) < 100 and    # Early/mid game
                 next_space > BOARD_WIDTH * 2)):         # Good space ahead
                use_boost = True

        move = f"{best_move}:BOOST" if use_boost else best_move
        
    return jsonify({"move": move}), 200

# @app.route("/send-move", methods=["GET"])
# def send_move():
#     """Improved survival + area-control agent."""
#     player_number = request.args.get("player_number", default=1, type=int)

#     with game_lock:
#         state = dict(LAST_POSTED_STATE)
#         my_agent = GLOBAL_GAME.agent1 if player_number == 1 else GLOBAL_GAME.agent2
#         opp_agent = GLOBAL_GAME.agent2 if player_number == 1 else GLOBAL_GAME.agent1

#         my_trail = list(my_agent.trail)
#         opp_trail = list(opp_agent.trail)
#         my_pos = my_trail[-1]
#         opp_pos = opp_trail[-1] if opp_trail else (10, 9)
#         my_dir = get_current_direction(my_trail)
#         occupied = set(my_trail + opp_trail)

#         BOARD_W, BOARD_H = 20, 18
#         dirs = ["UP", "DOWN", "LEFT", "RIGHT"]

#         def flood_score(pos):
#             """Fast BFS-based area count."""
#             from collections import deque
#             q = deque([pos])
#             seen = {pos}
#             while q and len(seen) < 250:
#                 x, y = q.popleft()
#                 for dx, dy in [(0,1),(0,-1),(1,0),(-1,0)]:
#                     nx, ny = (x+dx) % BOARD_W, (y+dy) % BOARD_H
#                     if (nx, ny) not in occupied and (nx, ny) not in seen:
#                         seen.add((nx, ny))
#                         q.append((nx, ny))
#             return len(seen)

#         def wall_distance(pos):
#             """How close we are to the nearest wall or trail."""
#             from math import inf
#             x, y = pos
#             dists = [x, BOARD_W - 1 - x, y, BOARD_H - 1 - y]
#             min_wall = min(dists)
#             near_trail = min(
#                 (abs(x - tx) + abs(y - ty))
#                 for (tx, ty) in occupied
#                 if (tx, ty) != pos
#             )
#             return min(min_wall, near_trail)

#         def manhattan(a, b):
#             return abs(a[0]-b[0]) + abs(a[1]-b[1])

#         safe_moves = []
#         for d in dirs:
#             if not is_valid_move(my_dir, d):
#                 continue
#             nxt = get_next_position(my_pos, d)
#             if nxt not in occupied:
#                 safe_moves.append(d)

#         if not safe_moves:
#             return jsonify({"move": my_dir}), 200

#         best_move = None
#         best_score = -1

#         for d in safe_moves:
#             nxt = get_next_position(my_pos, d)
#             space = flood_score(nxt)
#             wall_safety = wall_distance(nxt)
#             opp_proximity = manhattan(nxt, opp_pos)
#             # balance survival (space + wall distance) with keeping distance from opp
#             score = space + (5 * wall_safety) - (3 * max(0, 10 - opp_proximity))
#             if score > best_score:
#                 best_score = score
#                 best_move = d

#         # Smarter boost logic
#         use_boost = False
#         if my_agent.boosts_remaining > 0:
#             if len(safe_moves) <= 2 or wall_distance(my_pos) <= 2:
#                 use_boost = True

#         move = f"{best_move}:BOOST" if use_boost else best_move

#     return jsonify({"move": move}), 200



@app.route("/end", methods=["POST"])
def end_game():
    """Judge notifies agent that the match finished and provides final state."""
    data = request.get_json()
    if data:
        _update_local_game_from_post(data)
    return jsonify({"status": "acknowledged"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5008"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=False)
