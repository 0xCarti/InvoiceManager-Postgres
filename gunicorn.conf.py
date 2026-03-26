import os

# Bind to the port provided via the PORT environment variable, defaulting to
# 5000.
bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"

# Use eventlet workers to support WebSocket connections from Flask-SocketIO.
worker_class = "eventlet"

# When using WebSockets each connection can stay open indefinitely.  Gunicorn
# considers a worker unresponsive if it does not finish handling a request
# within the configured timeout, which causes long‑lived Socket.IO connections to
# be killed and leads to "Invalid session" errors as workers restart.

# Explicitly run a single worker and disable the timeout so that WebSocket
# connections are allowed to live for as long as needed without triggering
# worker restarts.
workers = 1
timeout = 0
