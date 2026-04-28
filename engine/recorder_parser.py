import os
import json
import re
import sys

def parse_and_inject(tc_id, description, flow_id, ats_root):
    """Parse temp_recording.py, extract dynamic data, and inject into flow."""
    temp_file = os.path.join(ats_root, "temp_recording.py")
    if not os.path.exists(temp_file):
        print(json.dumps({"status": "error", "message": "Recording file not found"}))
        return

    with open(temp_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Extract the body of the test_example function
    # The pytest codegen puts everything inside `def test_example(page: Page) -> None:`
    match = re.search(r'def test_[a-zA-Z0-9_]+\(.*?\)[^:]*:\s*(.*)', content, re.DOTALL)
    if not match:
        print(json.dumps({"status": "error", "message": "Could not parse generated code"}))
        return
    
    body_lines = match.group(1).strip().split('\n')
    
    # We want to replace hardcoded strings in .fill() and .type() with tc_data references
    data_dict = {}
    input_counter = 1
    processed_lines = []
    
    for line in body_lines:
        line = line.replace("    ", "", 1) # remove 1 level of indent
        
        # Regex to find .fill("value") or .fill('value')
        # We'll use a simplistic regex that works for typical codegen output
        fill_matches = re.finditer(r'\.fill\((["\'])(.*?)\1\)', line)
        for m in fill_matches:
            val = m.group(2)
            var_name = f"input_{input_counter}"
            data_dict[var_name] = val
            line = line.replace(f'.fill({m.group(1)}{val}{m.group(1)})', f'.fill(tc_data.get("{var_name}", ""))')
            input_counter += 1
            
        type_matches = re.finditer(r'\.type\((["\'])(.*?)\1', line)
        for m in type_matches:
            val = m.group(2)
            var_name = f"input_{input_counter}"
            data_dict[var_name] = val
            line = line.replace(f'.type({m.group(1)}{val}{m.group(1)}', f'.type(tc_data.get("{var_name}", "")')
            input_counter += 1
            
        processed_lines.append("    " + line)

    # Prepare paths
    flow_dir = os.path.join(ats_root, "tests", "flows", flow_id)
    os.makedirs(flow_dir, exist_ok=True)
    
    # 1. Update test_data.json
    data_file = os.path.join(flow_dir, "test_data.json")
    tc_data_json = {}
    if os.path.exists(data_file):
        with open(data_file, "r", encoding="utf-8") as f:
            try:
                tc_data_json = json.load(f)
            except:
                pass
    tc_data_json[tc_id] = data_dict
    with open(data_file, "w", encoding="utf-8") as f:
        json.dump(tc_data_json, f, indent=4)

    # 2. Update test_cases.json
    tc_file = os.path.join(flow_dir, "test_cases.json")
    tcs = []
    if os.path.exists(tc_file):
        with open(tc_file, "r", encoding="utf-8") as f:
            try:
                tcs = json.load(f)
            except:
                pass
    
    # Check if exists
    exists = False
    for tc in tcs:
        if tc.get("tc_id") == tc_id:
            tc["description"] = description
            exists = True
            break
    if not exists:
        tcs.append({"tc_id": tc_id, "description": description})
        
    with open(tc_file, "w", encoding="utf-8") as f:
        json.dump(tcs, f, indent=4)

    # 3. Append to test_<flow>.py
    test_py = os.path.join(flow_dir, f"test_{flow_id}.py")
    if not os.path.exists(test_py):
        with open(test_py, "w", encoding="utf-8") as f:
            f.write(f'"""{flow_id.replace("_", " ").title()} flow."""\n\n')
            f.write("import pytest\nfrom playwright.sync_api import expect, Page\n\n")

    underscored_id = tc_id.replace("-", "_")
    func_name = f"test_{underscored_id}"
    
    # Check if func already exists in file
    with open(test_py, "r", encoding="utf-8") as f:
        existing_code = f.read()
        
    if f"def {func_name}" in existing_code:
        # Complex to replace existing function via regex safely, so we'll just append a new one 
        # or fail if they use same ID. For now, we append. If duplicate, pytest might run both or last one.
        pass

    with open(test_py, "a", encoding="utf-8") as f:
        f.write(f'\n\n@pytest.mark.tc("{tc_id}")\n')
        f.write(f'def {func_name}(page: Page, tc_data, base_url):\n')
        f.write(f'    """{description}"""\n')
        f.write("\n".join(processed_lines) + "\n")

    # Cleanup temp file
    try:
        os.remove(temp_file)
    except:
        pass

    print(json.dumps({"status": "success", "tc_id": tc_id, "flow": flow_id, "data": data_dict}))

if __name__ == "__main__":
    if len(sys.argv) < 5:
        print(json.dumps({"status": "error", "message": "Missing arguments"}))
        sys.exit(1)
    
    # python recorder_parser.py <tc_id> <desc> <flow_id> <ats_root>
    parse_and_inject(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
