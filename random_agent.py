AGENT_NAME = "RandomBot"

@app.route("/send-move")
def move():
    return jsonify({"move": random.choice(["UP","DOWN","LEFT","RIGHT"])}),200
