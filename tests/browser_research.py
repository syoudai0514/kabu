"""Read real generated research output on a phone viewport; verify downloads/errors."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

expected=json.loads(Path('src/research-families-results.json').read_text())
trial_text=f"{expected['provenance']['total_trials']:,}"
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'), headless=True, args=['--no-sandbox'])
    page = browser.new_page(viewport={'width':390,'height':844}, is_mobile=True, has_touch=True)
    errors=[]
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(os.environ.get('KABU_TEST_URL','http://127.0.0.1:8000')+'/#research', wait_until='networkidle')
    page.locator('#research-dataset').select_option('etfs')
    page.get_by_role('heading',name='アルゴリズム研究室').wait_for()
    page.get_by_text(trial_text, exact=True).wait_for()
    assert '研究用・運用採用は保留' in page.inner_text('main')
    assert '2024-10-24' in page.inner_text('main')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile overflow'
    with page.expect_download() as download:
        page.locator('#research-export').click()
    report=json.loads(Path(download.value.path()).read_text(encoding='utf-8-sig'))
    assert report['provenance']['total_trials']==expected['provenance']['total_trials']
    assert len(report['families'])==9
    assert report['executable'] is False
    with page.expect_download() as download:
        page.locator('#research-config').click()
    config=json.loads(Path(download.value.path()).read_text(encoding='utf-8-sig'))
    assert config['executable'] is False
    assert config['candidate']['id']==report['overall_selected']['id']
    for family in report['families']:
        page.locator('#research-family').select_option(family['id'])
        page.get_by_role('heading',name='選択されたルール / '+family['selected']['id']).wait_for()
        detail=page.locator('section').filter(has=page.get_by_role('heading',name='100万円からの比較'))
        for window in family['windows']:
            amount=f"{round(window['selected']['final']):,}円"
            assert amount in detail.inner_text()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.reload(wait_until='networkidle')
    page.locator('#research-dataset').select_option('etfs')
    page.get_by_text(trial_text,exact=True).wait_for()
    Path('test-results').mkdir(exist_ok=True)
    page.screenshot(path='test-results/research-mobile.png',full_page=True)
    page.set_viewport_size({'width':1440,'height':1000})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path='test-results/research-desktop.png',full_page=True)
    page.route('**/src/research-families-results.json',lambda route: route.fulfill(status=503,body='unavailable'))
    page.reload(wait_until='networkidle')
    page.locator('#research-dataset').select_option('etfs')
    page.get_by_role('heading',name='研究結果を読み込めませんでした').wait_for()
    page.unroute('**/src/research-families-results.json')
    page.get_by_role('button',name='再読み込み').click()
    page.get_by_text(trial_text,exact=True).wait_for()
    assert not errors,errors
    browser.close()
    print('PASS: research results, mobile layout, report/config exports, persistence, fetch failure and recovery')
