"""Check the actual 500-candidate result through fetch, rendering and exports."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

expected=json.loads(Path('src/equity-screen-results.json').read_text())
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
    errors=[]
    page.on('pageerror',lambda error: errors.append(str(error)))
    page.goto(os.environ.get('KABU_TEST_URL','http://127.0.0.1:8000')+'/#research',wait_until='networkidle')
    page.get_by_text('1,488',exact=True).wait_for()
    assert '5年・10年は未評価' in page.inner_text('main')
    assert '後半でSPYを超えたのは 0 / 8' in page.inner_text('main')
    assert page.locator('#research-dataset').input_value()=='equities'
    for candidate in expected['selected']:
        page.locator('#equity-family').select_option(candidate['family'])
        section=page.locator('section').filter(has=page.get_by_role('heading',name='500候補・100万円からの比較',exact=True))
        for period in expected['periods']:
            assert f"{round(candidate[period]['final']):,}円" in section.inner_text()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),candidate['family']
        with page.expect_download() as saved:
            page.locator('#equity-config').click()
        config=json.loads(Path(saved.value.path()).read_text(encoding='utf-8-sig'))
        assert config['candidate']['id']==candidate['id'] and config['executable'] is False
    with page.expect_download() as saved:
        page.locator('#equity-export').click()
    report=json.loads(Path(saved.value.path()).read_text(encoding='utf-8-sig'))
    assert report==expected
    page.reload(wait_until='networkidle')
    page.get_by_text('1,488',exact=True).wait_for()
    assert page.locator('#equity-family').input_value()==next(x['family'] for x in expected['selected'] if x['id']==expected['overall_selected_id'])
    Path('test-results').mkdir(exist_ok=True)
    page.screenshot(path='test-results/equity-research-mobile.png',full_page=True)
    page.set_viewport_size({'width':1440,'height':1000})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path='test-results/equity-research-desktop.png',full_page=True)
    page.locator('#research-dataset').select_option('etfs')
    page.get_by_text('22,500',exact=True).wait_for()
    page.route('**/src/equity-screen-results.json',lambda route:route.fulfill(status=503,body='unavailable'))
    page.reload(wait_until='networkidle')
    page.get_by_role('heading',name='500銘柄の結果を読み込めませんでした').wait_for()
    page.unroute('**/src/equity-screen-results.json')
    page.locator('#equity-refresh').click()
    page.get_by_text('1,488',exact=True).wait_for()
    assert not errors,errors
    browser.close()
    print('PASS: 500-candidate mobile/desktop, all family metrics/configs, export, experiment toggle, reload, failure/retry')
