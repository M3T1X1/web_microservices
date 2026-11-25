"""Live browser checks for the local panel; creates UI-* test data and two accounts.

Requires: pip install playwright; playwright install chromium
Run against the full Compose system with an ADMIN account. Credentials stay out
of reports. Test accounts are disabled at the end; inventory data remains.
"""
import argparse
import json
from pathlib import Path
import re
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    parser.add_argument('--credentials-file', type=Path, required=True)
    parser.add_argument('--browser-executable', help='Optional existing Chromium executable')
    parser.add_argument('--output', type=Path, default=Path('reports/frontend'))
    args = parser.parse_args()
    credentials = json.loads(args.credentials_file.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    results, errors = {}, []

    with sync_playwright() as playwright:
        launch = {'headless': True}
        if args.browser_executable:
            launch['executable_path'] = args.browser_executable
        browser = playwright.chromium.launch(**launch)
        page = browser.new_page(viewport={'width': 1440, 'height': 1050})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_default_timeout(25000)
        page.goto(args.url, wait_until='networkidle')
        expect(page.locator('h1')).to_have_text('Sprawdź cały proces.')

        def login(account):
            page.locator('#session-open').click()
            page.locator('#login-email').fill(account['email'])
            page.locator('#login-password').fill(account['password'])
            page.locator('#login-submit').click()
            expect(page.locator('#session-dialog')).not_to_be_visible()
            expect(page.locator('#session-status')).to_contain_text(account['email'])

        def scenario(name):
            page.locator('[data-view="scenarios"]').click()
            page.locator(f'[data-scenario="{name}"]').click()
            expect(page.locator('#scenario-summary')).to_have_text(
                re.compile(r'^(PASS|FAIL) ·'), timeout=120000)
            summary = page.locator('#scenario-summary').inner_text()
            checks = page.locator('#scenario-results tr').all_text_contents()
            assert summary.startswith('PASS'), (summary, checks)
            (args.output / 'progress.json').write_text(json.dumps({**results, name: {'summary': summary, 'checks': checks}}, ensure_ascii=False, indent=2)+'\n')
            print(name+': '+summary, flush=True)
            return {'summary': summary, 'checks': checks}

        def operation(service, label):
            page.locator(f'[data-view="{service}"]').click()
            page.locator('#operations button').filter(has_text=label).click()

        def send(expected):
            page.locator('#expected-status').select_option(str(expected))
            with page.expect_response(lambda response: '/api/' in response.url and response.request.method != 'GET') as pending:
                page.locator('#send-request').click()
            response = pending.value
            assert response.status == expected, response.status
            expect(page.locator('#send-request')).to_be_enabled()
            expect(page.locator('#notice')).to_contain_text('Zgodnie z oczekiwaniem')
            return response.json()

        results['unauthenticated'] = scenario('auth')
        login(credentials)
        expect(page.locator('#session-status')).to_contain_text('ADMIN')
        results['admin_auth'] = scenario('auth')
        results['receipt'] = scenario('receipt')
        results['stock'] = scenario('stock')
        results['flow'] = scenario('flow')
        page.screenshot(path=str(args.output / 'desktop.png'))

        # Select a freshly created order and exercise the editable manual request.
        operation('outbound', 'Spakuj zamówienie')
        page.locator('#request-path').fill('/api/orders/' + str(uuid4()) + '/pack')
        send(404)
        results['manual_missing_order'] = 'HTTP 404'
        operation('inventory', 'Zmień ilość')
        page.locator('#request-body').fill('{invalid')
        before = page.locator('#history .history-entry').count()
        page.locator('#send-request').click()
        expect(page.locator('#notice')).to_contain_text('Nieprawidłowy JSON')
        assert page.locator('#history .history-entry').count() == before
        results['invalid_json'] = 'Rejected before network request'
        page.locator('#request-body').fill('{}')
        page.locator('#request-path').fill('https://example.com/api/stocks')
        page.locator('#send-request').click()
        expect(page.locator('#notice')).to_contain_text('Użyj ścieżki /api/')
        results['external_url'] = 'Rejected; session token stays on Gateway'

        accounts = []
        for role in ('WAREHOUSE', 'SHIPPING'):
            operation('auth', 'Utwórz użytkownika')
            account = {'email': f'ui-{uuid4().hex}@example.com',
                       'password': f'UI test {uuid4()}!', 'role': role}
            page.locator('#request-body').fill(json.dumps(account))
            created = send(201)
            # Log sanitization includes both request credentials and validation input.
            assert account['password'] not in page.locator('#response-body').inner_text()
            accounts.append((account, created['id']))
            (args.output / 'test-account-ids.json').write_text(json.dumps([item[1] for item in accounts]))
        page.locator('#logout').click()
        for account, user_id in accounts:
            login(account)
            results['role_' + account['role']] = scenario('auth')
            page.locator('#logout').click()
        login(credentials)
        for _, user_id in accounts:
            operation('auth', 'Zmień aktywność konta')
            page.locator('#field-user_id').fill(user_id)
            page.locator('#request-body').fill('{"is_active": false}')
            assert send(200)['is_active'] is False
        results['test_accounts'] = 'Created via panel and disabled after role checks'

        # A stock row fills the path; two reads expose MISS/HIT in the inspector.
        operation('inventory', 'Szczegóły stanu / cache')
        page.locator('#data-content tbody tr').first.click()
        assert '{stock_id}' not in page.locator('#request-path').input_value()
        for _ in range(2):
            page.locator('#send-request').click()
            expect(page.locator('#send-request')).to_be_enabled()
        expect(page.locator('#response-meta')).to_contain_text('X-Cache: HIT')
        results['cache'] = 'Second manual read: HIT'
        page.set_viewport_size({'width': 390, 'height': 844})
        page.locator('[data-view="scenarios"]').click()
        page.screenshot(path=str(args.output / 'mobile.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile page overflows viewport'
        results['mobile'] = '390px; no horizontal page overflow'
        page.locator('#logout').click()
        page.reload(wait_until='networkidle')
        expect(page.locator('#session-status')).to_have_text('Brak sesji')
        assert not page.evaluate('localStorage.length || sessionStorage.length')
        results['session'] = 'No persistent credentials or token'
        assert not errors, errors
        results['browser_errors'] = errors
        browser.close()

    (args.output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: value.get('summary') if isinstance(value, dict) else value for key, value in results.items()}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
