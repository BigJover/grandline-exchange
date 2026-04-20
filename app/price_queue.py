import queue

# Thread-safe queue: sync trade route puts updates here,
# async price_broadcaster drains it and broadcasts to WebSocket clients.
updates: queue.Queue = queue.Queue()
