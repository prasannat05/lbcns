from flask import Flask, render_template, request, jsonify, send_from_directory
import json
import math
import heapq
import os
from collections import defaultdict
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
ALLOWED_EXTENSIONS = {'geojson', 'json'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# GEO HELPERS
def haversine(a, b):
    lon1, lat1 = a
    lon2, lat2 = b
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(x), math.sqrt(1-x))

def bearing(a, b):
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1)*math.sin(lat2) - math.sin(lat1)*math.cos(lat2)*math.cos(dlon)
    brng = math.degrees(math.atan2(x, y))
    return (brng + 360) % 360

def turn_direction(b1, b2):
    diff = (b2 - b1 + 540) % 360 - 180
    if abs(diff) < 25:
        return "Go straight"
    elif diff > 0:
        return "Turn right"
    else:
        return "Turn left"

def build_graph(file_path):
    with open(file_path) as f:
        data = json.load(f)
    graph = defaultdict(list)
    nodes = {}
    for feat in data["features"]:
        if feat["geometry"]["type"] == "Point":
            name = feat["properties"]["name"].lower()
            coord = tuple(feat["geometry"]["coordinates"])
            nodes[name] = coord
    for feat in data["features"]:
        if feat["geometry"]["type"] == "LineString":
            name = feat["properties"]["name"].lower()
            if "-" not in name:
                continue
            a, b = name.split("-")
            coords = [tuple(c) for c in feat["geometry"]["coordinates"]]
            dist = 0
            for i in range(len(coords)-1):
                dist += haversine(coords[i], coords[i+1])
            graph[a].append((b, dist, coords))
            graph[b].append((a, dist, coords[::-1]))
    return graph, nodes

def shortest_path(graph, start, end):
    pq = [(0, start, [], [])]
    visited = set()
    while pq:
        cost, node, path, geoms = heapq.heappop(pq)
        if node in visited:
            continue
        path = path + [node]
        if node == end:
            return path, geoms
        visited.add(node)
        for nxt, w, coords in graph[node]:
            heapq.heappush(pq, (cost+w, nxt, path, geoms+[coords]))
    return None, None

def generate_instructions(path, geoms):
    instructions = []
    instructions.append({"text": f"Start at {path[0].capitalize()}", "type": "start"})
    
    prev_coords = None
    for i in range(len(geoms)):
        coords = geoms[i]
        dist = 0
        for j in range(len(coords)-1):
            dist += haversine(coords[j], coords[j+1])
        
        next_node = path[i+1] 
        is_junction = next_node.startswith("j")
        
        if i == 0:
            if is_junction:
                instructions.append({
                    "text": f"Continue straight {int(dist)} m to {next_node.upper()}",
                    "distance": int(dist),
                    "type": "straight",
                    "landmark": next_node
                })
            else:
                instructions.append({
                    "text": f"Go straight {int(dist)} m and cross {next_node.capitalize()}",
                    "distance": int(dist),
                    "type": "straight",
                    "landmark": next_node
                })
        else:
            b1 = bearing(prev_coords[-2], prev_coords[-1])
            b2 = bearing(coords[0], coords[1])
            turn = turn_direction(b1, b2)
            
            current_node = path[i]
            current_is_junction = current_node.startswith("j")
            
            if current_is_junction:
                instructions.append({
                    "text": f"At {current_node.upper()}, {turn}",
                    "type": "turn",
                    "turn_direction": turn.lower().replace(" ", "-")
                })
                
                if is_junction:
                    instructions.append({
                        "text": f"Go straight {int(dist)} m to {next_node.upper()}",
                        "distance": int(dist),
                        "type": "straight",
                        "landmark": next_node
                    })
                else:
                    instructions.append({
                        "text": f"Go straight {int(dist)} m and cross {next_node.capitalize()}",
                        "distance": int(dist),
                        "type": "straight",
                        "landmark": next_node
                    })
            else:
                if turn == "Go straight":
                    if is_junction:
                        instructions.append({
                            "text": f"Continue straight {int(dist)} m to {next_node.upper()}",
                            "distance": int(dist),
                            "type": "straight",
                            "landmark": next_node
                        })
                    else:
                        instructions.append({
                            "text": f"Continue straight {int(dist)} m and cross {next_node.capitalize()}",
                            "distance": int(dist),
                            "type": "straight",
                            "landmark": next_node
                        })
                else:
                    if is_junction:
                        instructions.append({
                            "text": f"{turn} and go {int(dist)} m to {next_node.upper()}",
                            "distance": int(dist),
                            "type": "turn-straight",
                            "turn_direction": turn.lower().replace(" ", "-"),
                            "landmark": next_node
                        })
                    else:
                        instructions.append({
                            "text": f"{turn} and go {int(dist)} m crossing {next_node.capitalize()}",
                            "distance": int(dist),
                            "type": "turn-straight",
                            "turn_direction": turn.lower().replace(" ", "-"),
                            "landmark": next_node
                        })
        
        prev_coords = coords
    
    instructions.append({"text": f"Reach {path[-1].upper()}", "type": "destination"})
    return instructions

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        return jsonify({"filename": filename, "message": "File uploaded successfully"})
    return jsonify({"error": "Invalid file type"}), 400

@app.route('/api/landmarks/<filename>')
def get_landmarks(filename):
    try:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if not os.path.exists(filepath):
            return jsonify({"error": "File not found"}), 404
        graph, nodes = build_graph(filepath)
        return jsonify(list(nodes.keys()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/route/<filename>', methods=['POST'])
def get_route(filename):
    try:
        data = request.json
        start = data['start'].lower()
        end = data['end'].lower()
        
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if not os.path.exists(filepath):
            return jsonify({"error": "GeoJSON file not found"}), 404
        
        graph, nodes = build_graph(filepath)
        
        if start not in nodes or end not in nodes:
            return jsonify({"error": f"Landmark not found: {start} or {end}"}), 400
        
        path, geoms = shortest_path(graph, start, end)
        
        if not path:
            return jsonify({"error": "No route found"}), 404
        
        instructions = generate_instructions(path, geoms)
        
        return jsonify({
            "route": " -> ".join(path),
            "instructions": instructions,
            "total_steps": len(instructions),
            "filename": filename
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
