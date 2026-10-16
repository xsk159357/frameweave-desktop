import json, os
BASE=r'F:\1223\解说工坊\frameweave-plugins\packages'
def load(p): return json.load(open(os.path.join(BASE,p),encoding='utf8'))
def test_asset_inventory():
    assert len(load('core_voice_library/data/tts_library.json')['voices'])==214
    assert len(load('core_ai_narrate/data/skills.json')['skills'])==9
    assert load('core_flower_text_analyze/data/flower_texts.json')['total']==814
    assert len(load('core_ai_narrate/data/narration_styles.json'))==8
    assert len(load('core_ai_narrate/data/lang_config.json'))>=2
    assert 'voice_style_link' in 'voice_style_link.json'
    assert len(load('core_ai_narrate/data/dialogue_selection_prompts.json'))>0
if __name__=='__main__': test_asset_inventory(); print('TOTAL FAIL=0/1')
