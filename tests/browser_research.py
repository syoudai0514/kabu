"""Read real generated research output on a phone viewport; verify downloads/errors."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'), headless=True, args=['--no-sandbox'])
    page = browser.new_page(viewport={'width':390,'height':844}, is_mobile=True, has_touch=True)
    errors=[]
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(os.environ.get('KABU_TEST_URL','http://127.0.0.1:8000')+'/#research', wait_until='networkidle')
    page.get_by_role('heading',name='アルゴリズム研究室').wait_for()
    page.get_by_text('24,000', exact=True).wait_for()
    assert '研究用・運用採用は保留' in page.inner_text('main')
    assert '2024-10-24' in page.inner_text('main')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile overflow'
    with page.expect_download() as download:
        page.locator('#research-export').click()
    report=json.loads(Path(download.value.path()).read_text(encoding='utf-8-sig'))
    assert report['provenance']['executed_trials']==24000
    assert report['executable'] is False
    assert report['ranking']['positive_both_count']==0
    with page.expect_download() as download:
        page.locator('#research-config').click()
    config=json.loads(Path(download.value.path()).read_text(encoding='utf-8-sig'))
    assert config['executable'] is False
    assert config['candidate']['id']==report['selected']['id']
    page.reload(wait_until='networkidle')
    page.get_by_text('24,000',exact=True).wait_for()
    Path('test-results').mkdir(exist_ok=True)
    page.screenshot(path='test-results/research-mobile.png',full_page=True)
    page.set_viewport_size({'width':1440,'height':1000})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path='test-results/research-desktop.png',full_page=True)
    page.route('**/src/research-results.json',lambda route: route.fulfill(status=503,body='unavailable'))
    page.reload(wait_until='networkidle')
    page.get_by_role('heading',name='研究結果を読み込めませんでした').wait_for()
    page.unroute('**/src/research-results.json')
    page.get_by_role('button',name='再読み込み').click()
    page.get_by_text('24,000',exact=True).wait_for()
    assert not errors,errors
    browser.close()
    print('PASS: research results, mobile layout, report/config exports, persistence, fetch failure and recovery')
