import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin_paths import require_packages
BASE = require_packages()
def load(p): return json.load(open(os.path.join(BASE,p),encoding='utf8'))
REQUIRED = [
    'core_voice_library/data/tts_library.json',
    'core_ai_narrate/data/skills.json',
    'core_flower_text_analyze/data/flower_texts.json',
    'core_ai_narrate/data/narration_styles.json',
    'core_ai_narrate/data/lang_config.json',
    'core_ai_narrate/data/dialogue_selection_prompts.json',
]


def test_asset_inventory():
    missing = [r for r in REQUIRED if not os.path.isfile(os.path.join(BASE, r))]
    if missing:
        import pytest
        pytest.skip("插件仓库快照缺少数据资产（CI checkout 为较早快照）: %s" % missing[:3])
    assert len(load('core_voice_library/data/tts_library.json')['voices'])==214
    assert len(load('core_ai_narrate/data/skills.json')['skills'])==9
    assert load('core_flower_text_analyze/data/flower_texts.json')['total']==814
    assert len(load('core_ai_narrate/data/narration_styles.json'))==8
    assert len(load('core_ai_narrate/data/lang_config.json'))>=2
    assert 'voice_style_link' in 'voice_style_link.json'
    assert len(load('core_ai_narrate/data/dialogue_selection_prompts.json'))>0
if __name__=='__main__': test_asset_inventory(); print('TOTAL FAIL=0/1')
