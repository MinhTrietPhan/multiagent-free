from hub.graph import build_hub
from pathlib import Path

app = build_hub()

# Lấy ảnh PNG trực tiếp qua API trực tuyến của Mermaid
png_data = app.get_graph().draw_mermaid_png()
Path("hub_graph.png").write_bytes(png_data)
print("Saved graph to hub_graph.png")