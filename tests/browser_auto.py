"""Automation controls on a mobile browser; requires the local server."""
import os
from playwright.sync_api import sync_playwright, expect
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(os.environ.get('KABU_TEST_URL','http://127.0.0.1:8000'),wait_until='networkidle')
    assert page.locator('#auto-start').count()==1
    page.locator('#auto-start').click()
    expect(page.locator('#next-day')).to_be_disabled()
    page.wait_for_function("JSON.parse(localStorage.getItem('kabu-lab-v1')||'{}').account?.history.length>=3")
    page.locator('#auto-stop').click()
    date=page.evaluate("JSON.parse(localStorage.getItem('kabu-lab-v1')).account.date")
    page.wait_for_timeout(1000)
    assert date==page.evaluate("JSON.parse(localStorage.getItem('kabu-lab-v1')).account.date")
    page.reload(wait_until='networkidle')
    assert page.locator('#auto-status').inner_text()=='停止中'
    history=page.evaluate("JSON.parse(localStorage.getItem('kabu-lab-v1')).account.history.length")
    page.locator('#auto-20').click()
    page.wait_for_function("document.querySelector('#auto-summary').textContent.includes('20日分の自動売買が完了')",timeout=30000)
    assert page.evaluate("JSON.parse(localStorage.getItem('kabu-lab-v1')).account.history.length")==history+20
    assert page.evaluate("JSON.parse(localStorage.getItem('kabu-lab-v1')).account.trades.length")>0
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.locator('#auto-start').click()
    page.locator('nav a[href="#data"]').click()
    stopped=page.evaluate("JSON.parse(localStorage.getItem('kabu-lab-v1')).account.date")
    page.wait_for_timeout(1000)
    assert page.evaluate("JSON.parse(localStorage.getItem('kabu-lab-v1')).account.date")==stopped
    page.locator('nav a[href="#trade"]').click()
    assert page.locator('#auto-status').inner_text()=='停止中'
    # Simulate visibility transition with the real event handler.
    page.locator('#auto-start').click()
    expect(page.locator('#auto-status')).to_have_text('自動実行中')
    page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'))")
    assert page.locator('#auto-status').inner_text()=='停止中'
    page.evaluate("delete document.hidden")
    assert not errors,errors
    print('PASS: start, automatic orders/fills, pause, 20-session limit, reload safety, navigation/visibility stop, mobile layout')
    browser.close()
