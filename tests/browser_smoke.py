"""Optional browser smoke test: pip install playwright; CHROMIUM_PATH=/usr/bin/chromium python tests/browser_smoke.py."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'), headless=True, args=['--no-sandbox'])
    page = browser.new_page(viewport={'width':390,'height':844}, device_scale_factor=1, is_mobile=True, has_touch=True)
    errors=[]
    page.on('pageerror', lambda err: errors.append(str(err)))
    page.goto(os.environ.get('KABU_TEST_URL','http://127.0.0.1:8000'), wait_until='networkidle')
    page.get_by_role('heading',name='運用の全体像').wait_for()
    def fits():
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Page overflows mobile viewport'
    fits()
    page.locator('a[href="#trade"]').first.click()
    page.locator('#order-symbol').select_option('AAPL')
    page.locator('#order-qty').fill('1')
    page.get_by_role('button',name='仮想注文を予約').click()
    assert page.locator('.order-card').count()==1
    page.locator('#next-day').click()
    assert page.locator('td .positive',has_text='約定').count()==1
    cash=page.evaluate("JSON.parse(localStorage.getItem('kabu-lab-v1')).account.cash")
    assert cash<1000000
    page.reload(wait_until='networkidle')
    assert page.locator('td .positive',has_text='約定').count()==1
    assert page.evaluate("JSON.parse(localStorage.getItem('kabu-lab-v1')).account.cash")==cash
    page.locator('#order-symbol').select_option('AAPL')
    page.locator('#order-side').select_option('SELL')
    page.locator('#order-qty').fill('1')
    page.get_by_role('button',name='仮想注文を予約').click()
    page.locator('#next-day').click()
    assert page.locator('td .positive',has_text='約定').count()==2
    with page.expect_download() as download:
        page.locator('#export-trades').click()
    assert download.value.suggested_filename=='kabu-paper-trades.csv'
    page.locator('a[href="#strategy"]').click()
    page.get_by_role('button',name='バックテストを実行').click()
    page.get_by_role('heading',name='戦略と買い持ちの比較').wait_for()
    fits()
    page.locator('a[href="#future"]').click()
    page.locator('#mc-preset').select_option('medium')
    assert page.locator('#mc-mu').input_value()=='8'
    page.locator('#mc-monthly').fill('10000')
    page.get_by_role('button',name='10年間をシミュレーション').click()
    assert '2,200,000' in page.locator('.stats').inner_text()
    fits()
    page.locator('a[href="#data"]').click()
    page.locator('#fee').fill('12')
    page.get_by_role('button',name='コストを保存').click()
    page.reload(wait_until='networkidle')
    assert page.locator('#fee').input_value()=='12'
    page.locator('#reset-initial').fill('2000000')
    page.locator('#reset-risk').select_option('medium')
    page.get_by_role('button',name='リセットする',exact=True).click()
    page.locator('#confirm-reset').click()
    assert page.evaluate("JSON.parse(localStorage.getItem('kabu-lab-v1')).account.initial")==2000000
    assert page.evaluate("JSON.parse(localStorage.getItem('kabu-lab-v1')).account.trades.length")==0
    with page.expect_download() as download:
        page.locator('#sample-csv').click()
    csv_path=download.value.path()
    page.once('dialog',lambda dialog:dialog.accept())
    page.locator('#csv-file').set_input_files(csv_path)
    page.get_by_text('CSVデータで検証中',exact=False).wait_for()
    assert page.evaluate("JSON.parse(localStorage.getItem('kabu-lab-v1')).data.source")=='csv'
    fits()
    page.once('dialog',lambda dialog:dialog.accept())
    page.locator('#restore-demo').click()
    page.locator('nav a[href="#overview"]').click()
    Path('test-results').mkdir(exist_ok=True)
    page.screenshot(path='test-results/mobile.png',full_page=True,animations='disabled')
    page.set_viewport_size({'width':1440,'height':1000})
    fits()
    page.screenshot(path='test-results/desktop.png',full_page=True,animations='disabled')
    assert not errors,errors
    print('PASS: mobile viewport, buy/sell, next-open fills, persistence, CSV export/import, backtest, Monte Carlo, settings, reset; no page errors')
    browser.close()
