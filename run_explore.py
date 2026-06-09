"""Interactive exploration launcher — maps admin + seller app, then authors test cases.
Run headlessly via: python run_explore.py
To send a message during the session, type it at the > prompt and press Enter.
"""
import os, sys, json, subprocess, queue, threading, time

ATS_ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHON   = os.path.join(ATS_ROOT, "venv", "Scripts", "python.exe")
AGENT    = os.path.join(ATS_ROOT, "engine", "agent_chat.py")
LOG_PATH = os.path.join(ATS_ROOT, "results", "_autonomous", "explore_run.jsonl")

PROMPT = """You are doing a full end-to-end exploration of two apps (Admin Panel + Seller App)
to both MAP the UI and then CREATE test cases. Read this first:

STEP 0 — Read the flow document:
  call read_context_file("COMPLETE_LLM_FLOW_CART_TO_DISPATCH.md")
  This gives you the complete UI flow from Cart creation → Payment → PO → Dispatch → Bills.
  Read it fully before proceeding.

TEST DATA (use exactly these values throughout):
  Customer:     Supertech Limited
  Ticket #:     60
  Quantity:     8
  Payment mode: Credit
  SKU:          Testing Ajay Seeds Nantes Testing Ajay Seeds 1.5 Ltr Printed 1.2LTR
  Seller:       M&M_Brand1  (for manual fulfilment step)

PHASE 1 — MAP + EXECUTE: Admin Panel Cart Creation
=====================================================================
Auth is pre-loaded. Navigate to https://admin-dev.agrim.app/
If login page appears: fill amit.dalal@agrim.app / Amit@12345, sign in, wait 5s.

1a. Navigate to OMS > Carts. observe — record:
    - Cart list URL
    - "Create Cart" button ref
    - Status filter tab refs

1b. Click "Create Cart". observe — record ALL form field refs:
    - Customer autocomplete ref
    - Shipping address dropdown ref
    - SKU search ref
    - Quantity input ref
    - Negotiation/ticket field ref
    - Payment method radio refs (Prepaid / COD / Credit)
    - EBA field ref
    - Save/Send buttons refs

1c. FILL THE FORM using the test data above:
    - Type "Supertech" in customer field → wait for autocomplete → select "Supertech Limited"
    - If asked for input at any point, ASK THE USER (say "I need input: <question>")
    - Select the shipping address (first available)
    - Search SKU: type "Testing Ajay Seeds Nantes" → select the matching SKU
    - Enter Quantity: 8
    - Apply Ticket: type 60 in the ticket field → click Apply
    - Select Payment: Credit
    - Note the EBA amount shown
    - Click "Save as Draft" (do NOT send to customer yet)
    - Record the Cart ID from the URL or success message

1d. Open the created/saved cart. observe — record:
    - Cart status badge ref
    - "Send to Customer" button ref
    - Payment status ref
    - Any "Approve" button refs

PHASE 2 — MAP: Payment/Finance Flow
=====================================================================
2a. On the cart detail, observe and record:
    - UTR submission form refs (if visible)
    - Finance approval refs
    - Any "Approve Payment" / "Confirm UTR" button refs

2b. Navigate to the Finance / Payments section in the sidebar. observe.
    Record: URL route, list columns, filter tabs, payment action button refs.

PHASE 3 — MAP: Parent Orders & Manual Fulfilment
=====================================================================
3a. Navigate to #/oms/parent/all or OMS > Orders/Parent Orders.
    observe — record: URL, search ref, filter tab refs.
    Note: MUI DataGrid rows are invisible — don't click on rows.

3b. Navigate to #/oms/parent/manual-fulfilment (or sidebar Manual Fulfilment).
    observe — record ALL form refs:
    - Order/SKU search ref
    - Seller autocomplete ref (where you'd select M&M_Brand1)
    - Source type dropdown ref
    - Cost price input ref
    - Quantity input ref
    - Pickup date ref
    - Confirm/Submit button ref

PHASE 4 — MAP: Purchase Orders (Admin side)
=====================================================================
4a. Navigate to #/oms/po. observe — status tab refs, search ref.
4b. Try to open any PO detail (use last page of pagination or search).
    observe on PO detail — record all visible action button refs.
4c. #/oms/po/ready-proofs — observe — approve/reject refs, file upload ref
4d. #/oms/po/tax-invoice — observe — approve/reject refs
4e. #/oms/po/pickup-proofs — observe — approve/reject refs

PHASE 5 — MAP: Bills / Payments (post-dispatch)
=====================================================================
5a. Look in sidebar for Bills, Payments, or Vendor Payments.
    Try #/oms/bills, #/payments, #/vendor-payments.
    observe — URL route, filter tabs, action button refs.

PHASE 6 — MAP: Seller App order detail at each status stage
=====================================================================
Navigate to https://supplier-dev.agrim.app/orders
For each tab that has orders, click any order to open the detail.
Record button refs at each stage:
  NEW:      Accept, Reject, Transaction Type, auto-cancel timer
  ACCEPTED: Pack button ("Pack at WH"), Change WH button
  PACKED:   Label Status text, Download Label, Ready button, Upload Tax Invoice, Upload Ready Proof
  READY:    Generate POP, Upload POP, Pickup Status, Scan Status
  DISPATCHED: all fields, PAID stamp

Skip any tab with 0 orders.

PHASE 7 — SAVE & CREATE TEST CASES
=====================================================================
7a. Call update_memory with all discovered URLs and element refs organized by section.

7b. Create the following test cases (use create_test_case for each):

TC-ORDERS-CART-001: Create Offline Cart
  Module: Cart
  Checkpoints:
    1. Navigate to cart list (#/oms/cart)
    2. Click Create Cart button
    3. Select customer "Supertech Limited" from autocomplete
    4. Select first available shipping address
    5. Search and select SKU "Testing Ajay Seeds Nantes Testing Ajay Seeds 1.5 Ltr Printed 1.2LTR"
    6. Enter quantity 8
    7. Apply negotiation ticket 60
    8. Select Credit payment method
    9. Verify EBA amount shown
    10. Click Save as Draft
    11. Verify cart created with Draft status

TC-ORDERS-CART-002: Send Cart to Customer
  Module: Cart
  Depends on: TC-ORDERS-CART-001 (cart in Draft)
  Checkpoints:
    1. Navigate to cart list, find the Draft cart
    2. Open cart detail
    3. Click "Send to Customer"
    4. Verify cart status changes to "Sent"

TC-ORDERS-PO-001: Accept Purchase Order (Seller Side)
  Module: PO
  Checkpoints:
    1. Navigate to seller app /orders, click "New" tab
    2. Open any order in New status
    3. Verify Accept and Reject buttons visible
    4. Click Accept
    5. Confirm in dialog
    6. Verify status changes to Accepted

TC-ORDERS-PO-002: Pack Purchase Order (Seller Side)
  Module: PO
  Checkpoints:
    1. Click "Accepted" tab on seller orders
    2. Open an accepted order
    3. Click Pack button
    4. Upload a ready proof image
    5. Confirm packing
    6. Verify status changes to Packed

TC-ORDERS-PO-003: Dispatch Purchase Order (Seller Side)
  Module: PO
  Checkpoints:
    1. Click "Ready for Dispatch" tab
    2. Open a ready order
    3. Verify Generate POP and Upload POP buttons visible
    4. (Admin has already approved ready proof)
    5. Generate shipping label if available
    6. Mark as dispatched with truck number and driver phone
    7. Verify status changes to Dispatched

ASK BEFORE CREATING: If any element refs are missing or you're unsure about a step,
ask the user: "I need clarification: <question>" and wait for the response.

When done, report: which test cases were created, which element refs were found,
and what gaps remain."""


env = os.environ.copy()
env["ATS_ROOT"]              = ATS_ROOT
env["PYTHONPATH"]            = ATS_ROOT
env["PYTHONUNBUFFERED"]      = "1"
env["ATS_AGENT_VISION"]      = "off"
env["ATS_AGENT_TOOL_BUDGET"] = "0"   # never pause

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
log_f = open(LOG_PATH, "w", encoding="utf-8")

def emit(obj):
    line = json.dumps(obj, ensure_ascii=True)
    log_f.write(line + "\n"); log_f.flush()
    try: print(line, flush=True)
    except UnicodeEncodeError: print(line.encode("ascii","replace").decode("ascii"), flush=True)

proc = subprocess.Popen(
    [PYTHON, AGENT], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT, env=env, cwd=ATS_ROOT,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
)

q = queue.Queue()
def reader():
    for raw in proc.stdout:
        q.put(raw.decode("utf-8","replace").rstrip())
    q.put(None)
threading.Thread(target=reader, daemon=True).start()

def send(d): proc.stdin.write((json.dumps(d)+"\n").encode()); proc.stdin.flush()

# Start session with headed=True so browser is visible
send({"action":"init","project_id":"test","env":"dev","provider":"mimo",
      "headed":True,"tool_budget":0})

# ── Interactive stdin relay: read user input and forward to agent ────────────
def stdin_reader():
    while True:
        try:
            line = sys.stdin.readline()
            if not line: break
            msg = line.strip()
            if msg:
                emit({"event":"user_input","message":msg})
                send({"action":"chat","message":msg})
        except Exception:
            break
threading.Thread(target=stdin_reader, daemon=True).start()

initialized = False
waiting_for_input = False

while True:
    try: raw = q.get(timeout=5)
    except queue.Empty: continue
    if raw is None: emit({"event":"launcher","message":"process exited"}); break
    try: obj = json.loads(raw)
    except: emit({"event":"raw","line":raw}); continue
    emit(obj)
    ev = obj.get("event","")

    # Print agent messages to console so user can see them and respond
    if ev == "assistant_message":
        text = obj.get("text","")
        print(f"\n[MIMO] {text}", flush=True)
        if "I need input" in text or "I need clarification" in text or "ask the user" in text.lower():
            print("\n>>> (type your response and press Enter) ", end="", flush=True)
            waiting_for_input = True
    elif ev == "tool_call":
        tool = obj.get("tool","")
        if tool: print(f"  [tool] {tool}", flush=True)
    elif ev == "log":
        msg = obj.get("message","")
        if msg: print(f"  {msg}", flush=True)

    if ev == "ready" and not initialized:
        initialized = True
        send({"action":"chat","message":PROMPT})
    if ev == "turn_complete":
        emit({"event":"launcher","message":"exploration complete"})
        print("\n[DONE] Turn complete. Type a message to continue, or Ctrl+C to exit.", flush=True)
        waiting_for_input = True
    if ev == "error":
        emit({"event":"launcher_error","message":obj.get("message","")})
        print(f"\n[ERROR] {obj.get('message','')}", flush=True)

try: proc.stdin.close()
except: pass
proc.wait(timeout=30)
log_f.close()
