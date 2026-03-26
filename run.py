import os
import sys

from app import create_app

app, socketio = create_app(sys.argv)

# Configure debug mode from environment (default to False)
debug = os.getenv("DEBUG", "False").lower() in {"1", "true", "t", "yes"}
app.debug = debug

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    socketio.run(app, host="0.0.0.0", port=port, debug=debug)
