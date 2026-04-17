"""Non-GUI pipeline test: VVM queue mechanics + HyProtocol round-trip."""
import sys, time, queue
sys.path.insert(0, '.')

from HyProtocol import HyProtocol
from HyVisionTools import HyLine, HyAnd
from RecipeTree import RecipeTree
from VirtualMachine import VirtualMachine, FolderProvider

# --- 1. Protocol round-trip ---
raw = HyProtocol.pack_command(HyProtocol.CMD_LIVE, target_id=1)
cmd = HyProtocol.unpack_command(raw)
assert cmd['cmd_id'] == HyProtocol.CMD_LIVE, f"cmd round-trip fail: {cmd}"
print("1. Protocol round-trip OK")

# --- 2. RecipeTree: add tools ---
tree = RecipeTree()
logic = HyAnd(tool_id=10)
tree.add_tool(logic, parent_id=0)
line = HyLine(tool_id=1)
tree.add_tool(line, parent_id=10)
assert 1 in tree.tool_index and 10 in tree.tool_index
print("2. RecipeTree add_tool OK")

# --- 3. VVM: start + queue ---
TEST_IMG_DIR = 'C:/Users/MSI/OneDrive/Documents/Dev/UI/HyVision_HD_PY/test_images'
provider = FolderProvider(TEST_IMG_DIR)
vm = VirtualMachine(provider)
vm.start()
time.sleep(0.1)

vm.cmd_queue.put(HyProtocol.pack_command(HyProtocol.CMD_LIVE))
time.sleep(1.5)

frames = 0
while True:
    try:
        pkt = vm.rst_queue.get_nowait()
        frames += 1
    except queue.Empty:
        break

vm.cmd_queue.put(HyProtocol.pack_command(HyProtocol.CMD_STOP))
time.sleep(0.1)
vm.running = False
vm.quit()
vm.wait(2000)

assert frames > 0, "VVM produced 0 frames!"
print(f"3. VVM live frames: {frames} OK")

print("ALL TESTS PASSED")
