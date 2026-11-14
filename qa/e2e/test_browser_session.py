"""Real Chromium/API boundary coverage, not the product's page flows."""


def _fetch(page, url, *, method="GET", credentials="include", body=None):
    return page.evaluate("""async ({url, method, credentials, body}) => {
        const response = await fetch(url, {
            method, credentials, signal: AbortSignal.timeout(10000),
            ...(body === null ? {} : {
                headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
            })
        });
        return {status: response.status, text: await response.text()};
    }""", dict(url=url, method=method, credentials=credentials, body=body))


def test_cross_origin_session_cookie_and_logout(live_api, browser_context):
    page = browser_context.new_page()
    page.goto(live_api.allowed_origin)
    assert _fetch(page, live_api.url + "/me")["status"] == 401
    login = _fetch(page, live_api.url + "/auth/login", method="POST", body={
        "email": "sam@demo.stitch", "password": "stitch-demo",
    })
    assert login["status"] == 200, login
    session = next(cookie for cookie in browser_context.cookies(live_api.url)
                   if cookie["name"] == "stitch_session")
    assert session["httpOnly"] is True
    assert session["sameSite"] == "Lax"
    assert "stitch_session" not in page.evaluate("document.cookie")
    assert _fetch(page, live_api.url + "/me")["status"] == 200
    assert _fetch(page, live_api.url + "/me", credentials="omit")["status"] == 401
    assert _fetch(page, live_api.url + "/auth/logout", method="POST")["status"] == 204
    assert _fetch(page, live_api.url + "/me")["status"] == 401
    assert not any(cookie["name"] == "stitch_session" for cookie in browser_context.cookies(live_api.url))


def test_disallowed_origin_cannot_complete_login_preflight(live_api, browser_context):
    page = browser_context.new_page()
    page.goto(live_api.denied_origin)
    result = page.evaluate("""async (url) => {
        try {
            await fetch(url + '/auth/login', {
                method: 'POST', credentials: 'include',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({email: 'sam@demo.stitch', password: 'stitch-demo'}),
                signal: AbortSignal.timeout(10000)
            });
            return 'unexpected success';
        } catch (error) { return error.name; }
    }""", live_api.url)
    assert result == "TypeError", result
    assert not any(cookie["name"] == "stitch_session" for cookie in browser_context.cookies(live_api.url))
    # The allowed origin proves rejection did not silently establish a session.
    page.goto(live_api.allowed_origin)
    assert _fetch(page, live_api.url + "/me")["status"] == 401
