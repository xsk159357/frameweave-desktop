import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.comfy_convert import comfy_to_fw, fw_to_comfy

def test_roundtrip_twenty_nodes():
    prompt={str(i):{"class_type":"user/web_demo","inputs":{"text":f"x{i}"}} for i in range(20)}
    wf=comfy_to_fw({"prompt":prompt,"name":"r"})
    out=fw_to_comfy(wf)
    assert len(out["prompt"]) == 20
    assert all(v["class_type"]=="user/web_demo" for v in out["prompt"].values())
    assert out["prompt"]["3"]["inputs"]["text"] == "x3"

def test_link_roundtrip():
    doc={"prompt":{"1":{"class_type":"user/web_demo","inputs":{"text":["2",0]}},"2":{"class_type":"user/web_demo","inputs":{"text":"root"}}}}
    wf=comfy_to_fw(doc); out=fw_to_comfy(wf)
    assert out["prompt"]["1"]["inputs"]["text"] == ["2",0]

if __name__ == '__main__':
    test_roundtrip_twenty_nodes(); test_link_roundtrip(); print('TOTAL FAIL=0/2')
