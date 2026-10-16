import asyncio, json, os, tempfile, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_nine_skills_session_continue_duration():
    import importlib.util
    p=r'F:\1223\解说工坊\frameweave-plugins\packages\user_jsm_narration_studio\node.py'
    spec=importlib.util.spec_from_file_location('studio',p); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    skills=m.load_skills(); assert len(skills)==9
    from app.assets import create_store
    d=os.path.join(os.path.dirname(__file__),'_narration_studio_fixture'); os.makedirs(d,exist_ok=True)
    try:
        store=create_store('local',os.path.join(d,'assets'))
        ctx={'store':store,'progress':lambda x:None}
        node=m.NarrationStudioNode()
        a=asyncio.run(node.run(ctx,{}, {'skill':'jingxuan','duration':'short','session_id':'s1','stream':True}))
        b=asyncio.run(node.run(ctx,{}, {'skill':'horror_comedy','duration':'long','session_id':'s1','continuation':'续写下一段','stream':True}))
        assert a['report']['skill']=='jingxuan' and b['report']['skill']=='horror_comedy'
        assert b['report']['continued'] is True and b['report']['duration']=='long'
        assert os.path.isfile(b['report']['session_path'])
    finally:
        import shutil; shutil.rmtree(d,ignore_errors=True)

if __name__=='__main__': test_nine_skills_session_continue_duration(); print('TOTAL FAIL=0/1')
