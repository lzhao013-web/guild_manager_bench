"""经营画像浏览器验收，由 smoke_compare.py 共用本地测试服务。"""
import json
from playwright.sync_api import expect


def check_profile(page, base, data, requests, screenshots=None):
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(base + "/replay/profile.html?run=b")
    expect(page.locator("#profileView .profile-heading h2")).to_have_text(data["b"]["agent"]["config"]["model"])
    expect(page.locator("#profileView .profile-insights")).to_contain_text("先锋 获得 100%")
    expect(page.locator('[data-profile-card="training"]')).to_contain_text("80 经验")
    expect(page.locator('[data-profile-card="habits"]')).to_contain_text('该次尝试内未见后续调用')
    page.locator('.profile-insights article').first.get_by_role('button').first.click()
    expect(page.locator('[data-proof-heading]')).to_have_text('第 1 回合 · 第 1 次尝试')
    expect(page.locator('.proof-action.selected')).to_contain_text('分配 80 经验给 先锋')
    expect(page.locator('.proof-action.selected')).to_have_attribute('open', '')
    expect(page.locator('[data-proof-turn]')).to_have_value('1')
    page.locator('[data-proof-turn]').select_option('2')
    expect(page.locator('[data-proof-heading]')).to_have_text('第 2 回合 · 第 2 次尝试')
    page.locator('[data-proof-attempt]').select_option('0')
    expect(page.locator('[data-proof-body]')).to_contain_text('empty_response_limit')
    expect(page.locator('[data-proof-body]')).to_contain_text('未找到装备实例')
    if screenshots:
        page.evaluate('window.scrollTo(0, 0)')
        page.screenshot(path=str(screenshots / 'profile-desktop.png'), full_page=True)

    page.goto(base + '/replay/profile.html?run=real')
    expect(page.locator('.profile-heading h2')).to_have_text('真实存档协议测试')
    real_cost = data['real']['turns'][0]['observation_before']['recruit_candidates'][0]['recruit_gold']
    expect(page.locator('[data-profile-card="investment"]')).to_contain_text(str(real_cost))
    api_count = sum(path.startswith('/api/') for _, path in requests)
    page.locator('#profileSource > summary').click()
    imported = json.loads(json.dumps(data['b']))
    payload = '<img src=x onerror="window.__profileInjected=1">'
    imported['agent']['config']['model'] = payload
    imported['turns'][0]['observation_before']['adventurers'][0]['name'] = payload
    page.locator('#profileFile').set_input_files({'name': 'profile-local.json', 'mimeType': 'application/json', 'buffer': json.dumps(imported).encode()})
    expect(page.locator('.profile-heading h2')).to_have_text(payload)
    expect(page.locator('.profile-insights')).to_contain_text(payload)
    assert page.evaluate('window.__profileInjected') is None
    assert page.locator('#profileView img').count() == 0
    assert sum(path.startswith('/api/') for _, path in requests) == api_count
    page.locator('#profileSource > summary').click()
    page.locator('#profileFile').set_input_files({'name': 'invalid.json', 'mimeType': 'application/json', 'buffer': b'{}'})
    expect(page.locator('#profileError')).to_contain_text('请选择 LLM')
    expect(page.locator('#profileLoadStatus')).to_contain_text('保留上一份画像')

    # 对照页使用已加载的数据生成画像，包括本地文件，不额外请求服务器。
    page.goto(base + '/replay/compare.html?left=a&right=b')
    expect(page.locator('#turnTitle')).to_have_text('第 1 回合')
    page.locator('#sourcePicker > summary').click()
    page.locator('#fileB').set_input_files({'name': 'local-b.json', 'mimeType': 'application/json', 'buffer': json.dumps(data['b']).encode()})
    expect(page.locator('#sourceB')).to_contain_text('local-b.json')
    api_count = sum(path.startswith('/api/') for _, path in requests)
    page.get_by_role('button', name='查看 B 经营画像').click()
    expect(page.get_by_role('dialog')).to_be_visible()
    expect(page.locator('#comparisonProfile .profile-insights')).to_contain_text('先锋 获得 100%')
    page.locator('#comparisonProfile .profile-insights article').first.get_by_role('button').first.click()
    expect(page.locator('#comparisonProfile .proof-action.selected')).to_contain_text('80')
    page.keyboard.press('Escape')
    expect(page.get_by_role('dialog')).not_to_be_visible()
    expect(page.locator('#comparisonProfile')).to_be_empty()
    page.get_by_role('button', name='查看 A 经营画像').click()
    expect(page.locator('#comparisonProfile .profile-heading h2')).to_have_text(data['a']['agent']['config']['model'])
    # 关闭事件异步派发，旧清理不能清空已经重新打开的另一侧画像。
    page.evaluate("""async () => {
        const dialog = document.getElementById('profileDialog');
        const closed = new Promise(resolve => dialog.addEventListener('close', resolve, {once: true}));
        dialog.close();
        document.querySelector('[data-profile-side="B"]').click();
        await closed;
    }""")
    expect(page.locator('#comparisonProfile .profile-heading h2')).to_have_text(data['b']['agent']['config']['model'])
    page.get_by_role('button', name='关闭', exact=True).click()
    assert sum(path.startswith('/api/') for _, path in requests) == api_count

    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(base + '/replay/profile.html?run=b&turn=2')
    expect(page.locator('[data-proof-heading]')).to_have_text('第 2 回合 · 第 2 次尝试')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    if screenshots:
        page.evaluate('window.scrollTo(0, 0)')
        page.screenshot(path=str(screenshots / 'profile-mobile.png'), full_page=True)
    print('Profile browser smoke passed: facts, step evidence, attempts, real archive, local files, dialog reuse, XSS, mobile.')
