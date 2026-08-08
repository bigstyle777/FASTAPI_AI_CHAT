const API_BASE_URL = "http://127.0.0.1:8001";
const THEME_STORAGE_KEY = 'aichatpro-theme';
const VALID_THEMES = ['light', 'dark', 'system'];
let systemThemeMedia = window.matchMedia('(prefers-color-scheme: dark)');

let currentSessionId = null;
let isSendingMessage = false;
let currentCaptchaId = null;
let currentSessionHasMessages = false;
let editingMessageId = null;
let currentAbortController = null;
let isStopping = false;
let currentUser = null;
let currentView = 'chat';
let adminData = {
    dashboard: null,
    users: [],
    roles: [],
    permissions: [],
};

// ===== Markdown 娓叉煋妯″潡 =====
let mdInstance = null;
let mermaidInitialized = false;

function getMarkdownInstance() {
    if (mdInstance) return mdInstance;
    if (!window.markdownit) return null;
    mdInstance = window.markdownit({
        html: false,
        breaks: true,
        linkify: true,
        highlight(code, lang) {
            const language = (lang || '').toLowerCase();
            if (language === 'mermaid') {
                return `<pre class="mermaid-pre"><code class="language-mermaid">${window.markdownit.utils.escapeHtml(code)}</code></pre>`;
            }
            if (window.hljs && language && window.hljs.getLanguage(language)) {
                try {
                    return `<pre class="hljs"><code class="hljs language-${language}">${window.hljs.highlight(code, { language }).value}</code></pre>`;
                } catch (_) { }
            }
            if (window.hljs) {
                try {
                    return `<pre class="hljs"><code class="hljs">${window.hljs.highlightAuto(code).value}</code></pre>`;
                } catch (_) { }
            }
            return `<pre class="hljs"><code>${window.markdownit.utils.escapeHtml(code)}</code></pre>`;
        }
    });
    if (window.markdownitKatex) {
        mdInstance.use(window.markdownitKatex);
    }
    return mdInstance;
}

function renderMarkdownContent(text, container, isStreaming = false) {
    const md = getMarkdownInstance();
    if (!md || !text) {
        container.textContent = text || '';
        return;
    }
    try {
        container.innerHTML = md.render(text);
    } catch (error) {
        console.warn('Markdown render failed:', error);
        container.textContent = text;
        return;
    }
    container.classList.add('markdown-body');
    container.removeAttribute('data-stream-stable-text');
    if (!isStreaming) {
        renderMermaidBlocks(container);
        addCodeCopyButtons(container);
    }
}

function findUnclosedFenceStart(text) {
    const fencePattern = /^([`~]{3,})(.*)$/;
    const lines = text.split(/(\n)/);
    let offset = 0;
    let openFence = null;

    for (let i = 0; i < lines.length; i += 2) {
        const line = lines[i] || '';
        const newline = lines[i + 1] || '';
        const match = line.match(fencePattern);
        if (match) {
            const marker = match[1];
            const fence = {
                char: marker[0],
                length: marker.length,
                start: offset,
            };

            if (!openFence) {
                openFence = fence;
            } else if (
                fence.char === openFence.char &&
                fence.length >= openFence.length
            ) {
                openFence = null;
            }
        }
        offset += line.length + newline.length;
    }

    return openFence ? openFence.start : -1;
}

function findUnclosedInlineCodeStart(text, limit) {
    let openIndex = -1;
    for (let i = 0; i < limit; i += 1) {
        if (text[i] !== '`') continue;
        if (i > 0 && text[i - 1] === '\\') continue;

        let end = i + 1;
        while (end < limit && text[end] === '`') {
            end += 1;
        }

        if (openIndex === -1) {
            openIndex = i;
        } else {
            openIndex = -1;
        }
        i = end - 1;
    }
    return openIndex;
}

function findUnclosedBlockMathStart(text, limit) {
    let openIndex = -1;
    for (let i = 0; i < limit - 1; i += 1) {
        if (text[i] !== '$' || text[i + 1] !== '$') continue;
        if (i > 0 && text[i - 1] === '\\') continue;
        openIndex = openIndex === -1 ? i : -1;
        i += 1;
    }
    return openIndex;
}

function findUnclosedInlineMathStart(text, limit) {
    let openIndex = -1;
    for (let i = 0; i < limit; i += 1) {
        if (text[i] !== '$') continue;
        if (i > 0 && text[i - 1] === '\\') continue;
        if (text[i + 1] === '$' || text[i - 1] === '$') continue;
        openIndex = openIndex === -1 ? i : -1;
    }
    return openIndex;
}

function findStreamingMarkdownBoundary(text) {
    if (!text) return 0;

    let safeLimit = text.length;
    const unclosedFenceStart = findUnclosedFenceStart(text);
    if (unclosedFenceStart >= 0) {
        safeLimit = Math.min(safeLimit, unclosedFenceStart);
    }

    const unclosedCodeStart = findUnclosedInlineCodeStart(text, safeLimit);
    if (unclosedCodeStart >= 0) {
        safeLimit = Math.min(safeLimit, unclosedCodeStart);
    }

    const unclosedMathStart = findUnclosedBlockMathStart(text, safeLimit);
    if (unclosedMathStart >= 0) {
        safeLimit = Math.min(safeLimit, unclosedMathStart);
    }

    const unclosedInlineMathStart = findUnclosedInlineMathStart(text, safeLimit);
    if (unclosedInlineMathStart >= 0) {
        safeLimit = Math.min(safeLimit, unclosedInlineMathStart);
    }

    const blockBoundary = text.lastIndexOf('\n\n', safeLimit);
    if (blockBoundary >= 0) {
        return blockBoundary + 2;
    }

    const lineBoundary = text.lastIndexOf('\n', safeLimit);
    if (lineBoundary >= 80) {
        return lineBoundary + 1;
    }

    return safeLimit;
}

function renderStreamingMarkdownContent(text, container) {
    const md = getMarkdownInstance();
    if (!md || !text) {
        container.textContent = text || '';
        return;
    }

    const boundary = findStreamingMarkdownBoundary(text);
    const stableText = text.slice(0, boundary);
    const tailText = text.slice(boundary);

    try {
        if (stableText && container.dataset.streamStableText !== stableText) {
            container.innerHTML = md.render(stableText);
            container.classList.add('markdown-body');
            container.dataset.streamStableText = stableText;
        } else if (!stableText) {
            container.textContent = '';
            container.classList.add('markdown-body');
            container.dataset.streamStableText = '';
        }

        let tail = Array.from(container.children).find((child) =>
            child.classList.contains('markdown-stream-tail')
        );
        if (tailText) {
            if (!tail) {
                tail = document.createElement('span');
                tail.className = 'markdown-stream-tail';
                container.appendChild(tail);
            }
            tail.textContent = tailText;
        } else if (tail) {
            tail.remove();
        }
    } catch (error) {
        console.warn('Streaming markdown render failed:', error);
        container.textContent = text;
    }
}

function addCodeCopyButtons(container) {
    container.querySelectorAll('pre').forEach((pre) => {
        if (pre.classList.contains('mermaid-pre')) return;
        if (pre.querySelector('.code-copy-btn')) return;
        const code = pre.querySelector('code');
        if (!code) return;
        pre.classList.add('code-block');
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'code-copy-btn';
        btn.textContent = '澶嶅埗';
        btn.title = '澶嶅埗浠ｇ爜';
        btn.addEventListener('click', async () => {
            try {
                await navigator.clipboard.writeText(code.textContent);
                btn.textContent = '已复制';
            } catch (_) {
                btn.textContent = '澶辫触';
            }
            setTimeout(() => { btn.textContent = '澶嶅埗'; }, 1500);
        });
        pre.appendChild(btn);
    });
}

async function renderMermaidBlocks(container) {
    if (!window.mermaid) return;
    const codeBlocks = Array.from(container.querySelectorAll('pre.mermaid-pre code.language-mermaid'));
    if (codeBlocks.length === 0) return;
    if (!mermaidInitialized) {
        const theme = resolveTheme(getStoredTheme()) === 'dark' ? 'dark' : 'default';
        window.mermaid.initialize({ startOnLoad: false, theme, securityLevel: 'loose' });
        mermaidInitialized = true;
    }
    for (let i = 0; i < codeBlocks.length; i++) {
        const codeEl = codeBlocks[i];
        const pre = codeEl.parentElement;
        const graphDef = codeEl.textContent;
        const wrapper = document.createElement('div');
        wrapper.className = 'mermaid-wrapper';
        const target = document.createElement('div');
        wrapper.appendChild(target);
        pre.replaceWith(wrapper);
        try {
            const id = `mermaid-${Date.now()}-${i}`;
            const { svg } = await window.mermaid.render(id, graphDef);
            target.innerHTML = svg;
        } catch (err) {
            target.textContent = '娴佺▼鍥炬覆鏌撳け璐? ' + (err.message || err);
        }
    }
}

function switchMarkdownTheme(resolved) {
    const light = document.getElementById('md-theme-light');
    const dark = document.getElementById('md-theme-dark');
    if (light) light.disabled = resolved === 'dark';
    if (dark) dark.disabled = resolved !== 'dark';
    if (mermaidInitialized && window.mermaid) {
        window.mermaid.initialize({ startOnLoad: false, theme: resolved === 'dark' ? 'dark' : 'default', securityLevel: 'loose' });
    }
}

function getStoredTheme() {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return VALID_THEMES.includes(stored) ? stored : 'system';
}

function resolveTheme(theme) {
    if (theme === 'system') {
        return systemThemeMedia.matches ? 'dark' : 'light';
    }
    return theme;
}

function applyTheme(theme) {
    const resolved = resolveTheme(theme);
    document.documentElement.setAttribute('data-theme', resolved);
    document.querySelectorAll('.theme-btn').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.theme === theme);
    });
    switchMarkdownTheme(resolved);
}

function setTheme(theme) {
    const next = VALID_THEMES.includes(theme) ? theme : 'system';
    localStorage.setItem(THEME_STORAGE_KEY, next);
    applyTheme(next);
}

function initTheme() {
    const theme = getStoredTheme();
    applyTheme(theme);
    systemThemeMedia.addEventListener('change', () => {
        if (getStoredTheme() === 'system') {
            applyTheme('system');
        }
    });
}

function getToken() {
    return localStorage.getItem('token');
}

function setToken(token) {
    localStorage.setItem('token', token);
}

function removeToken() {
    localStorage.removeItem('token');
}

function isLoggedIn() {
    return !!getToken();
}

function showMessageNotice(message, type = 'info') {
    const notice = document.getElementById('messageNotice');
    if (!notice) return;
    notice.textContent = message;
    notice.className = `notice ${type}`;
}

function clearMessageNotice() {
    showMessageNotice('', 'info');
}

function setLoadingState(isLoading, buttonId = 'sendBtn') {
    const button = document.getElementById(buttonId);
    if (!button) return;

    const defaultText = {
        sendBtn: '发送',
        loginBtn: '鐧诲綍',
        registerBtn: '娉ㄥ唽',
    };

    if (buttonId === 'sendBtn') {
        if (isLoading) {
            button.disabled = false;
            button.classList.add('stop-mode');
            button.setAttribute('aria-label', '鍋滄鐢熸垚');
            button.textContent = '';
        } else {
            button.disabled = false;
            button.classList.remove('stop-mode');
            button.setAttribute('aria-label', '发送消息');
            button.textContent = defaultText[buttonId];
        }
        return;
    }

    button.disabled = isLoading;
    button.textContent = isLoading ? '澶勭悊涓?..' : (defaultText[buttonId] || '纭畾');
}

function renderEmptyChat() {
    const messagesContainer = document.getElementById('messages');
    messagesContainer.innerHTML = `
        <div class="empty-state">
            <div class="empty-icon">AI</div>
            <h2>寮€濮嬩竴娆℃柊鐨勫璇?/h2>
            <p>閫夋嫨宸︿晶浼氳瘽锛屾垨鏂板缓鑱婂ぉ鍚庤緭鍏ヤ綘鐨勯棶棰樸€?/p>
        </div>
    `;
}

function showLoginPage() {
    document.getElementById('loginPage').style.display = 'flex';
    document.getElementById('mainPage').style.display = 'none';
    document.getElementById('profilePage').style.display = 'none';
    hideAdminView();
    loadCaptcha();
}

function showMainPage() {
    document.getElementById('loginPage').style.display = 'none';
    document.getElementById('mainPage').style.display = 'flex';
    document.getElementById('profilePage').style.display = 'none';
    showChatView();
    loadAuthContext();
}

function showProfilePage() {
    document.getElementById('loginPage').style.display = 'none';
    document.getElementById('mainPage').style.display = 'none';
    document.getElementById('profilePage').style.display = 'flex';
    loadProfile();
}

function showChatView() {
    currentView = 'chat';
    const chatView = document.getElementById('chatView');
    const adminView = document.getElementById('adminView');
    if (chatView) chatView.style.display = 'flex';
    if (adminView) adminView.style.display = 'none';
    updateSidebarState();
    loadSessions();
    loadSettings();
}

function showAdminView() {
    if (!currentUser || currentUser.role !== 'admin') {
        showMessageNotice('没有管理员权限', 'error');
        return;
    }
    currentView = 'admin';
    const chatView = document.getElementById('chatView');
    const adminView = document.getElementById('adminView');
    if (chatView) chatView.style.display = 'none';
    if (adminView) adminView.style.display = 'flex';
    updateSidebarState();
    loadAdminDashboard();
}

function hideAdminView() {
    const adminView = document.getElementById('adminView');
    if (adminView) adminView.style.display = 'none';
}

function clearAuthState() {
    removeToken();
    currentSessionId = null;
    currentSessionHasMessages = false;
    currentUser = null;
    adminData = { dashboard: null, users: [], roles: [], permissions: [] };
    showLoginPage();
}

function updateSidebarState() {
    const adminBtn = document.getElementById('adminBtn');
    const backToChatBtn = document.getElementById('backToChatBtn');
    const profileBtn = document.getElementById('profileBtn');
    const currentRoleLabel = document.getElementById('currentRoleLabel');
    const userNameLabel = document.getElementById('sidebarUserName');

    const isAdmin = currentUser && currentUser.role === 'admin';
    if (adminBtn) {
        adminBtn.style.display = isAdmin ? 'flex' : 'none';
    }
    if (backToChatBtn) {
        backToChatBtn.style.display = currentView === 'admin' ? 'flex' : 'none';
    }
    if (profileBtn) {
        profileBtn.style.display = currentView === 'admin' ? 'none' : 'flex';
    }
    if (currentRoleLabel) {
        currentRoleLabel.textContent = currentUser ? currentUser.role : '--';
    }
    if (userNameLabel) {
        userNameLabel.textContent = currentUser ? currentUser.username : '--';
    }
}

async function apiCall(url, options = {}, requestOptions = {}) {
    const { auth = true, handleUnauthorized = true } = requestOptions;
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };

    const token = auth ? getToken() : null;
    if (token) {
        headers.Authorization = `Bearer ${token}`;
    }

    try {
        const response = await fetch(`${API_BASE_URL}${url}`, {
            ...options,
            headers
        });

        if (response.status === 401 && handleUnauthorized) {
            showMessageNotice('登录已过期，请重新登录', 'error');
            clearAuthState();
            return null;
        }

        return response;
    } catch (error) {
        if (error.name === 'AbortError') {
            return null;
        }
        console.error('缃戠粶璇锋眰澶辫触:', error);
        showMessageNotice('缃戠粶璇锋眰澶辫触锛岃妫€鏌ユ湇鍔″櫒鏄惁鍚姩', 'error');
        return null;
    }
}

async function loadCaptcha() {
    const captchaImage = document.getElementById('captchaImage');
    const captchaInput = document.getElementById('loginCaptchaCode');
    if (!captchaImage) return;

    currentCaptchaId = null;
    captchaImage.removeAttribute('src');

    try {
        const response = await apiCall('/users/captcha', { method: 'POST' }, {
            auth: false,
            handleUnauthorized: false
        });
        if (!response) return;

        const data = await response.json();
        if (!response.ok || !data.success) {
            showMessageNotice(data.message || '验证码加载失败', 'error');
            return;
        }

        currentCaptchaId = data.captcha_id;
        captchaImage.src = data.image;
        if (captchaInput) {
            captchaInput.value = '';
        }
    } catch (error) {
        console.error('鍔犺浇楠岃瘉鐮佸け璐?', error);
        showMessageNotice('验证码加载失败', 'error');
    }
}

async function loadSettings() {
    try {
        const response = await apiCall('/users/settings');
        if (!response) return;

        const data = await response.json();
        const apiKeyInput = document.getElementById('apiKeyInput');
        const providerSelect = document.getElementById('providerSelect');
        if (apiKeyInput) {
            apiKeyInput.value = data.api_key || '';
        }
        if (providerSelect) {
            providerSelect.value = data.provider || 'deepseek';
        }
    } catch (error) {
        console.error('鍔犺浇璁剧疆澶辫触:', error);
    }
}

async function loadProfile() {
    try {
        const [userResponse, settingsResponse] = await Promise.all([
            apiCall('/users/me'),
            apiCall('/users/settings')
        ]);

        if (userResponse) {
            const userData = await userResponse.json();
            document.getElementById('profileUsername').textContent = userData.username || '--';
            document.getElementById('profileUserId').textContent = userData.user_id || '--';
        }

        if (settingsResponse) {
            const settingsData = await settingsResponse.json();
            document.getElementById('apiKeyInput').value = settingsData.api_key || '';
            document.getElementById('providerSelect').value = settingsData.provider || 'deepseek';
        }
    } catch (error) {
        console.error('鍔犺浇涓汉淇℃伅澶辫触:', error);
    }
}

function toggleApiKeyVisibility() {
    const input = document.getElementById('apiKeyInput');
    const button = document.getElementById('toggleApiKeyBtn');
    if (!input || !button) return;

    if (input.type === 'password') {
        input.type = 'text';
        button.textContent = '闅愯棌';
    } else {
        input.type = 'password';
        button.textContent = '鏄剧ず';
    }
}

async function loadAuthContext() {
    try {
        const response = await apiCall('/users/me');
        if (!response) return;

        const userData = await response.json();
        if (!response.ok || !userData.success) {
            return;
        }

        currentUser = {
            user_id: userData.user_id,
            username: userData.username,
            role: userData.role || 'user',
            permissions: Array.isArray(userData.permissions) ? userData.permissions : [],
        };
        updateSidebarState();
    } catch (error) {
        console.error('loadAuthContext failed:', error);
    }
}

async function saveSettings() {
    const apiKey = document.getElementById('apiKeyInput').value.trim();
    const provider = document.getElementById('providerSelect').value;

    try {
        const response = await apiCall('/users/settings', {
            method: 'POST',
            body: JSON.stringify({ api_key: apiKey, provider })
        });
        if (!response) return;

        const data = await response.json();
        showMessageNotice(data.success ? '设置已保存' : '保存设置失败', data.success ? 'success' : 'error');
    } catch (error) {
        console.error('淇濆瓨璁剧疆澶辫触:', error);
        showMessageNotice('淇濆瓨璁剧疆澶辫触', 'error');
    }
}

async function handleRegister() {
    const username = document.getElementById('registerUsername').value.trim();
    const password = document.getElementById('registerPassword').value;

    if (!username || !password) {
        showMessageNotice('请输入用户名和密码', 'error');
        return;
    }

    setLoadingState(true, 'registerBtn');
    try {
        const response = await apiCall('/users/register', {
            method: 'POST',
            body: JSON.stringify({ username, password })
        }, { auth: false });
        if (!response) return;

        const data = await response.json();
        if (data.success) {
            showMessageNotice('娉ㄥ唽鎴愬姛锛岃鐧诲綍', 'success');
            document.getElementById('registerUsername').value = '';
            document.getElementById('registerPassword').value = '';
            document.getElementById('loginTab').click();
        } else {
            showMessageNotice(data.message || '娉ㄥ唽澶辫触', 'error');
        }
    } catch (error) {
        console.error('娉ㄥ唽澶辫触:', error);
        showMessageNotice('娉ㄥ唽澶辫触锛岃閲嶈瘯', 'error');
    } finally {
        setLoadingState(false, 'registerBtn');
    }
}

async function handleLogin() {
    const username = document.getElementById('loginUsername').value.trim();
    const password = document.getElementById('loginPassword').value;
    const captchaCode = document.getElementById('loginCaptchaCode').value.trim();

    if (!username || !password || !captchaCode || !currentCaptchaId) {
        showMessageNotice('请输入用户名、密码和验证码', 'error');
        return;
    }

    setLoadingState(true, 'loginBtn');
    try {
        const response = await apiCall('/users/login', {
            method: 'POST',
            body: JSON.stringify({
                username,
                password,
                captcha_id: currentCaptchaId,
                captcha_code: captchaCode
            })
        }, { auth: false });
        if (!response) return;

        const data = await response.json();
        if (data.success && data.access_token) {
            setToken(data.access_token);
            currentCaptchaId = null;
            document.getElementById('loginUsername').value = '';
            document.getElementById('loginPassword').value = '';
            document.getElementById('loginCaptchaCode').value = '';
            showMainPage();
            showMessageNotice('鐧诲綍鎴愬姛', 'success');
        } else {
            showMessageNotice(data.message || '鐧诲綍澶辫触', 'error');
            await loadCaptcha();
        }
    } catch (error) {
        console.error('鐧诲綍澶辫触:', error);
        showMessageNotice('鐧诲綍澶辫触锛岃閲嶈瘯', 'error');
        await loadCaptcha();
    } finally {
        setLoadingState(false, 'loginBtn');
    }
}

async function handleLogout() {
    if (getToken()) {
        await apiCall('/users/logout', { method: 'POST' }, { handleUnauthorized: false });
    }
    clearAuthState();
}

let activeSessionMenu = null;

function closeSessionMenu() {
    if (activeSessionMenu) {
        activeSessionMenu.remove();
        activeSessionMenu = null;
    }
    document.removeEventListener('click', onDocumentClickCloseMenu);
}

function onDocumentClickCloseMenu(event) {
    if (activeSessionMenu && !activeSessionMenu.contains(event.target) && !event.target.classList.contains('session-menu-trigger')) {
        closeSessionMenu();
    }
}

function openSessionMenu(triggerBtn, sessionId, titleText, isPinned = false) {
    closeSessionMenu();
    closeMessageMenu();

    const menu = document.createElement('div');
    menu.className = 'session-menu';

    const pinItem = document.createElement('button');
    pinItem.type = 'button';
    pinItem.className = 'session-menu-item';
    pinItem.textContent = isPinned ? '鍙栨秷缃《' : '缃《鑱婂ぉ';
    pinItem.addEventListener('click', (event) => {
        event.stopPropagation();
        closeSessionMenu();
        toggleSessionPin(sessionId, !isPinned);
    });

    const renameItem = document.createElement('button');
    renameItem.type = 'button';
    renameItem.className = 'session-menu-item';
    renameItem.textContent = '重命名会话';
    renameItem.addEventListener('click', (event) => {
        event.stopPropagation();
        closeSessionMenu();
        openRenameSessionModal(sessionId, titleText);
    });

    const deleteItem = document.createElement('button');
    deleteItem.type = 'button';
    deleteItem.className = 'session-menu-item danger';
    deleteItem.textContent = '鍒犻櫎浼氳瘽';
    deleteItem.addEventListener('click', (event) => {
        event.stopPropagation();
        closeSessionMenu();
        deleteSession(sessionId);
    });

    menu.appendChild(pinItem);
    menu.appendChild(renameItem);
    menu.appendChild(deleteItem);

    document.body.appendChild(menu);
    const rect = triggerBtn.getBoundingClientRect();
    menu.style.top = `${rect.bottom + 4}px`;
    menu.style.left = `${rect.right - menu.offsetWidth}px`;
    if (menu.offsetLeft < 8) {
        menu.style.left = '8px';
    }

    activeSessionMenu = menu;
    document.addEventListener('click', onDocumentClickCloseMenu);
}

async function toggleSessionPin(sessionId, isPinned) {
    try {
        const response = await apiCall(`/chat/sessions/${sessionId}`, {
            method: 'PATCH',
            body: JSON.stringify({ is_pinned: isPinned })
        });
        if (!response) return;

        const data = await response.json();
        if (!response.ok || !data.success) {
            showMessageNotice(data.message || '缃《澶辫触', 'error');
            return;
        }

        await loadSessions();
        showMessageNotice(isPinned ? '已置顶' : '已取消置顶', 'success');
    } catch (error) {
        console.error('缃《澶辫触:', error);
        showMessageNotice('缃《澶辫触', 'error');
    }
}

let activeMessageMenu = null;

function closeMessageMenu() {
    if (activeMessageMenu) {
        activeMessageMenu.remove();
        activeMessageMenu = null;
    }
    document.removeEventListener('click', onDocumentClickCloseMessageMenu);
}

function onDocumentClickCloseMessageMenu(event) {
    if (activeMessageMenu && !activeMessageMenu.contains(event.target) && !event.target.classList.contains('message-menu-trigger')) {
        closeMessageMenu();
    }
}

function hasTokenUsage(usage) {
    return !!usage && Number(usage.total_tokens || 0) > 0;
}

function normalizeTokenUsage(source = {}) {
    return {
        model: source.model || null,
        prompt_tokens: Number(source.prompt_tokens || 0),
        completion_tokens: Number(source.completion_tokens || 0),
        total_tokens: Number(source.total_tokens || 0),
    };
}

function createTokenUsageElement(usage) {
    const tokenUsage = normalizeTokenUsage(usage);
    const wrapper = document.createElement('div');
    wrapper.className = 'token-usage';

    const summary = document.createElement('button');
    summary.type = 'button';
    summary.className = 'token-usage-summary';
    summary.setAttribute('aria-expanded', 'false');

    const label = document.createElement('span');
    label.textContent = `total_tokens: ${tokenUsage.total_tokens}`;

    const arrow = document.createElement('span');
    arrow.className = 'token-usage-arrow';
    arrow.textContent = 'v';

    summary.appendChild(label);
    summary.appendChild(arrow);

    const detail = document.createElement('div');
    detail.className = 'token-usage-detail';
    detail.hidden = true;
    detail.innerHTML = `
        <div>model: ${tokenUsage.model || '--'}</div>
        <div>prompt_tokens: ${tokenUsage.prompt_tokens}</div>
        <div>completion_tokens: ${tokenUsage.completion_tokens}</div>
        <div>total_tokens: ${tokenUsage.total_tokens}</div>
    `;

    summary.addEventListener('click', () => {
        const isOpen = !detail.hidden;
        detail.hidden = isOpen;
        summary.setAttribute('aria-expanded', String(!isOpen));
        wrapper.classList.toggle('open', !isOpen);
    });

    wrapper.appendChild(summary);
    wrapper.appendChild(detail);
    return wrapper;
}

function attachTokenUsage(messageGroup, usage) {
    if (!messageGroup || !hasTokenUsage(usage)) return;

    const existing = messageGroup.querySelector('.token-usage');
    if (existing) {
        existing.replaceWith(createTokenUsageElement(usage));
        return;
    }

    const actionsWrapper = messageGroup.querySelector('.message-actions');
    const usageElement = createTokenUsageElement(usage);
    if (actionsWrapper) {
        messageGroup.insertBefore(usageElement, actionsWrapper);
    } else {
        messageGroup.appendChild(usageElement);
    }
}

function createMessageGroup(role, content, messageId, usage = null) {
    const group = document.createElement('div');
    group.className = `message-group ${role}`;
    group.dataset.messageId = messageId;
    const isInherited = !!(usage && usage.is_inherited);

    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${role}`;
    if (role === 'assistant') {
        renderMarkdownContent(content, msgDiv);
    } else {
        msgDiv.textContent = content;
    }
    group.appendChild(msgDiv);

    if (role === 'assistant') {
        attachTokenUsage(group, usage);
    }

    if (isInherited && role !== 'user') {
        return group;
    }

    const actionsWrapper = document.createElement('div');
    actionsWrapper.className = 'message-actions';

    if (role === 'assistant') {
        const likeBtn = document.createElement('button');
        likeBtn.type = 'button';
        likeBtn.className = 'message-action-btn like-btn';
        likeBtn.setAttribute('aria-label', '鐐硅禐');
        likeBtn.title = '鐐硅禐';
        likeBtn.innerHTML = '&#128077;';
        likeBtn.addEventListener('click', (event) => {
            event.stopPropagation();
            toggleFeedback(likeBtn, 'like');
        });

        const dislikeBtn = document.createElement('button');
        dislikeBtn.type = 'button';
        dislikeBtn.className = 'message-action-btn dislike-btn';
        dislikeBtn.setAttribute('aria-label', '点踩');
        dislikeBtn.title = '点踩';
        dislikeBtn.innerHTML = '&#128078;';
        dislikeBtn.addEventListener('click', (event) => {
            event.stopPropagation();
            toggleFeedback(dislikeBtn, 'dislike');
        });

        const copyBtn = document.createElement('button');
        copyBtn.type = 'button';
        copyBtn.className = 'message-action-btn copy-btn';
        copyBtn.setAttribute('aria-label', '澶嶅埗');
        copyBtn.title = '澶嶅埗';
        copyBtn.innerHTML = '&#128203;';
        copyBtn.addEventListener('click', async (event) => {
            event.stopPropagation();
            await copyMessageContent(content);
        });

        actionsWrapper.appendChild(likeBtn);
        actionsWrapper.appendChild(dislikeBtn);
        actionsWrapper.appendChild(copyBtn);
    }

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'message-menu-trigger';
    trigger.setAttribute('aria-label', '娑堟伅鎿嶄綔');
    trigger.innerHTML = '&#8942;';
    trigger.title = '鏇村鎿嶄綔';
    trigger.addEventListener('click', (event) => {
        event.stopPropagation();
        openMessageMenu(trigger, messageId, role, content, isInherited);
    });
    actionsWrapper.appendChild(trigger);

    group.appendChild(actionsWrapper);

    return group;
}

function toggleFeedback(targetBtn, type) {
    const wrapper = targetBtn.closest('.message-actions');
    if (!wrapper) return;

    const likeBtn = wrapper.querySelector('.like-btn');
    const dislikeBtn = wrapper.querySelector('.dislike-btn');

    const wasActive = targetBtn.classList.contains('active');
    likeBtn.classList.remove('active');
    dislikeBtn.classList.remove('active');

    if (!wasActive) {
        targetBtn.classList.add('active');
        showMessageNotice(type === 'like' ? '已点赞' : '已点踩', 'success');
    }
}

async function copyMessageContent(content) {
    try {
        await navigator.clipboard.writeText(content);
        showMessageNotice('已复制到剪贴板', 'success');
    } catch (error) {
        console.error('澶嶅埗澶辫触:', error);
        showMessageNotice('澶嶅埗澶辫触', 'error');
    }
}

function openMessageMenu(triggerBtn, messageId, role, content, isInherited = false) {
    closeMessageMenu();
    closeSessionMenu();

    const menu = document.createElement('div');
    menu.className = 'message-menu';

    if (role === 'user' && !isInherited) {
        const modifyItem = document.createElement('button');
        modifyItem.type = 'button';
        modifyItem.className = 'message-menu-item';
        modifyItem.textContent = '淇敼娑堟伅';
        modifyItem.addEventListener('click', (event) => {
            event.stopPropagation();
            closeMessageMenu();
            editingMessageId = messageId;
            const input = document.getElementById('messageInput');
            input.value = content;
            input.focus();
            showMessageNotice('宸茶繘鍏ヤ慨鏀硅娑堟伅鐘舵€侊紝鍙戦€佸悗灏嗘洿鏂板師娑堟伅锛屽苟閲嶆柊鐢熸垚鍥炲', 'info');
        });
        menu.appendChild(modifyItem);

    }

    if (role === 'user') {
        const branchItem = document.createElement('button');
        branchItem.type = 'button';
        branchItem.className = 'message-menu-item';
        branchItem.textContent = '在新对话中建立分支';
        branchItem.addEventListener('click', (event) => {
            event.stopPropagation();
            closeMessageMenu();
            createMessageBranch(messageId);
        });
        menu.appendChild(branchItem);
    }

    if (role === 'assistant') {
        const branchItem = document.createElement('button');
        branchItem.type = 'button';
        branchItem.className = 'message-menu-item';
        branchItem.textContent = '在新分支中新建对话';
        branchItem.addEventListener('click', (event) => {
            event.stopPropagation();
            closeMessageMenu();
            createMessageBranch(messageId);
        });
        menu.appendChild(branchItem);
    }

    if (!isInherited) {
        const deleteItem = document.createElement('button');
        deleteItem.type = 'button';
        deleteItem.className = 'message-menu-item danger';
        deleteItem.textContent = '鍒犻櫎娑堟伅';
        deleteItem.addEventListener('click', (event) => {
            event.stopPropagation();
            closeMessageMenu();
            deleteMessage(messageId);
        });
        menu.appendChild(deleteItem);
    }

    document.body.appendChild(menu);
    const rect = triggerBtn.getBoundingClientRect();
    menu.style.top = `${rect.bottom + 4}px`;
    if (role === 'user') {
        menu.style.left = `${rect.right - menu.offsetWidth}px`;
    } else {
        menu.style.left = `${rect.left}px`;
    }
    if (menu.offsetLeft < 8) {
        menu.style.left = '8px';
    }
    if (menu.offsetLeft + menu.offsetWidth > window.innerWidth - 8) {
        menu.style.left = `${window.innerWidth - menu.offsetWidth - 8}px`;
    }

    activeMessageMenu = menu;
    document.addEventListener('click', onDocumentClickCloseMessageMenu);
}

async function deleteMessage(messageId) {
    if (!messageId) {
        showMessageNotice('鏃犳硶鍒犻櫎锛氭秷鎭疘D缂哄け', 'error');
        return;
    }

    if (!window.confirm('纭畾鍒犻櫎杩欐潯娑堟伅鍚楋紵')) {
        return;
    }

    try {
        const response = await apiCall(`/chat/messages/${messageId}`, { method: 'DELETE' });
        if (!response) return;

        const data = await response.json();
        if (!response.ok || !data.success) {
            showMessageNotice(data.message || '鍒犻櫎娑堟伅澶辫触', 'error');
            return;
        }

        showMessageNotice(data.message || '消息已删除', 'success');
        await loadSessionMessages(currentSessionId);
    } catch (error) {
        console.error('鍒犻櫎娑堟伅澶辫触:', error);
        showMessageNotice('鍒犻櫎娑堟伅澶辫触', 'error');
    }
}

async function createMessageBranch(messageId) {
    if (isSendingMessage) {
        showMessageNotice('消息发送中，请稍后再创建分支', 'error');
        return;
    }

    try {
        const response = await apiCall(
            `/chat/messages/${messageId}/branch`,
            { method: 'POST' }
        );
        if (!response) return;

        const data = await response.json();
        if (!response.ok || !data.success) {
            showMessageNotice(data.message || '寤虹珛鍒嗘敮澶辫触', 'error');
            return;
        }

        currentSessionId = data.session_id;
        currentSessionHasMessages = true;
        await loadSessions();
        showMessageNotice('宸插湪鏂板璇濅腑寤虹珛鍒嗘敮', 'success');
    } catch (error) {
        console.error('寤虹珛鍒嗘敮澶辫触:', error);
        showMessageNotice('寤虹珛鍒嗘敮澶辫触', 'error');
    }
}

async function createSessionBranch() {
    if (isSendingMessage) {
        showMessageNotice('消息发送中，请稍后再创建分支', 'error');
        return;
    }

    if (!currentSessionId) {
        showMessageNotice('褰撳墠娌℃湁鍙垎鏀殑浼氳瘽', 'error');
        return;
    }

    try {
        const response = await apiCall(
            `/chat/sessions/${currentSessionId}/branch`,
            { method: 'POST' }
        );
        if (!response) return;

        const data = await response.json();
        if (!response.ok || !data.success) {
            showMessageNotice(data.message || '寤虹珛鍒嗘敮瀵硅瘽澶辫触', 'error');
            return;
        }

        currentSessionId = data.session_id;
        currentSessionHasMessages = true;
        await loadSessions();
        showMessageNotice('宸插湪鏂板垎鏀腑鏂板缓瀵硅瘽', 'success');
    } catch (error) {
        console.error('寤虹珛鍒嗘敮瀵硅瘽澶辫触:', error);
        showMessageNotice('寤虹珛鍒嗘敮瀵硅瘽澶辫触', 'error');
    }
}

function createSessionItem(session) {
    const sessionId = Number(session.session_id);
    const title = session.title || '新会话';
    const lastMessage = session.last_message || '鏆傛棤娑堟伅';
    const isPinned = !!session.is_pinned;
    const sessionItem = document.createElement('div');
    sessionItem.className = 'session-item';
    sessionItem.dataset.sessionId = sessionId;
    if (isPinned) {
        sessionItem.classList.add('pinned');
    }
    if (Number(currentSessionId) === sessionId) {
        sessionItem.classList.add('active');
    }

    const row = document.createElement('div');
    row.className = 'session-row';

    const content = document.createElement('div');
    content.className = 'session-content';

    const titleElement = document.createElement('div');
    titleElement.className = 'session-title';
    titleElement.textContent = title;

    const lastMessageElement = document.createElement('div');
    lastMessageElement.className = 'session-last-message';
    lastMessageElement.textContent = lastMessage;

    const menuTrigger = document.createElement('button');
    menuTrigger.type = 'button';
    menuTrigger.className = 'session-menu-trigger';
    menuTrigger.setAttribute('aria-label', '浼氳瘽鎿嶄綔');
    menuTrigger.innerHTML = '&#8942;';
    menuTrigger.title = '鏇村鎿嶄綔';
    menuTrigger.addEventListener('click', (event) => {
        event.stopPropagation();
        openSessionMenu(menuTrigger, sessionId, titleElement.textContent, isPinned);
    });

    content.appendChild(titleElement);
    content.appendChild(lastMessageElement);
    row.appendChild(content);
    row.appendChild(menuTrigger);
    sessionItem.appendChild(row);
    sessionItem.onclick = () => loadSessionMessages(sessionId);
    return sessionItem;
}

function renderActiveSessionStub(sessionId, title = '新会话') {
    const sessionList = document.getElementById('sessionList');
    const emptySessions = sessionList.querySelector('.empty-sessions');
    if (emptySessions) {
        emptySessions.remove();
    }

    document.querySelectorAll('.session-item').forEach((item) => {
        item.classList.remove('active');
    });

    const existingItem = sessionList.querySelector(`.session-item[data-session-id="${sessionId}"]`);
    if (existingItem) {
        existingItem.remove();
    }

    const sessionItem = createSessionItem({
        session_id: sessionId,
        title,
        last_message: '鏆傛棤娑堟伅'
    });
    sessionList.prepend(sessionItem);
}

async function deleteEmptyCurrentSession(nextSessionId = null) {
    if (!currentSessionId || currentSessionHasMessages) {
        return false;
    }

    if (nextSessionId && Number(currentSessionId) === Number(nextSessionId)) {
        return false;
    }

    const sessionId = currentSessionId;
    try {
        const response = await apiCall(`/chat/session/${sessionId}`, { method: 'DELETE' });
        if (response) {
            await response.json().catch(() => null);
        }
    } catch (error) {
        console.error('鑷姩鍒犻櫎绌轰細璇濆け璐?', error);
    }

    const sessionItem = document.querySelector(`.session-item[data-session-id="${sessionId}"]`);
    if (sessionItem) {
        sessionItem.remove();
    }

    if (Number(currentSessionId) === Number(sessionId)) {
        currentSessionId = null;
        currentSessionHasMessages = false;
    }
    return true;
}

function openRenameSessionModal(sessionId, currentTitle) {
    closeRenameSessionModal();

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.id = 'renameSessionModal';

    const modal = document.createElement('div');
    modal.className = 'modal-card';

    const heading = document.createElement('h3');
    heading.textContent = '重命名会话';

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'modal-input';
    input.value = currentTitle || '新会话';
    input.maxLength = 100;
    input.placeholder = '请输入会话名称';

    const actions = document.createElement('div');
    actions.className = 'modal-actions';

    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'modal-btn ghost';
    cancelBtn.textContent = '鍙栨秷';

    const saveBtn = document.createElement('button');
    saveBtn.type = 'button';
    saveBtn.className = 'modal-btn primary';
    saveBtn.textContent = '淇濆瓨';

    actions.appendChild(cancelBtn);
    actions.appendChild(saveBtn);
    modal.appendChild(heading);
    modal.appendChild(input);
    modal.appendChild(actions);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    input.focus();
    input.select();

    const close = () => closeRenameSessionModal();
    cancelBtn.addEventListener('click', close);
    overlay.addEventListener('click', (event) => {
        if (event.target === overlay) close();
    });
    input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            saveBtn.click();
        } else if (event.key === 'Escape') {
            event.preventDefault();
            close();
        }
    });

    saveBtn.addEventListener('click', async () => {
        const newTitle = input.value.trim();
        if (!newTitle) {
            showMessageNotice('浼氳瘽鍚嶇О涓嶈兘涓虹┖', 'error');
            input.focus();
            return;
        }
        if (newTitle === currentTitle) {
            closeRenameSessionModal();
            return;
        }

        saveBtn.disabled = true;
        cancelBtn.disabled = true;
        saveBtn.textContent = '淇濆瓨涓?..';

        try {
            const response = await apiCall(`/chat/sessions/${sessionId}`, {
                method: 'PATCH',
                body: JSON.stringify({ title: newTitle })
            });
            if (!response) {
                saveBtn.disabled = false;
                cancelBtn.disabled = false;
                saveBtn.textContent = '淇濆瓨';
                return;
            }

            const data = await response.json();
            if (!response.ok || !data.success) {
                showMessageNotice(data.message || '淇敼浼氳瘽鍚嶇О澶辫触', 'error');
                saveBtn.disabled = false;
                cancelBtn.disabled = false;
                saveBtn.textContent = '淇濆瓨';
                return;
            }

            const titleElement = document.querySelector(`.session-item[data-session-id="${sessionId}"] .session-title`);
            if (titleElement) {
                titleElement.textContent = newTitle;
            }
            closeRenameSessionModal();
            showMessageNotice('会话名称已更新', 'success');
        } catch (error) {
            console.error('淇敼浼氳瘽鍚嶇О澶辫触:', error);
            showMessageNotice('淇敼浼氳瘽鍚嶇О澶辫触', 'error');
            saveBtn.disabled = false;
            cancelBtn.disabled = false;
            saveBtn.textContent = '淇濆瓨';
        }
    });
}

function closeRenameSessionModal() {
    const modal = document.getElementById('renameSessionModal');
    if (modal) {
        modal.remove();
    }
}

async function deleteSession(sessionId) {
    if (!window.confirm('确定删除该会话吗？所有消息记录也会一并删除。')) {
        return;
    }

    try {
        const response = await apiCall(`/chat/session/${sessionId}`, { method: 'DELETE' });
        if (!response) return;

        const data = await response.json();
        if (!response.ok || !data.success) {
            showMessageNotice(data.message || '鍒犻櫎浼氳瘽澶辫触', 'error');
            return;
        }

        if (Number(currentSessionId) === Number(sessionId)) {
            currentSessionId = null;
            currentSessionHasMessages = false;
            renderEmptyChat();
        }
        await loadSessions();
        showMessageNotice(data.message || '会话已删除', 'success');
    } catch (error) {
        console.error('鍒犻櫎浼氳瘽澶辫触:', error);
        showMessageNotice('鍒犻櫎浼氳瘽澶辫触', 'error');
    }
}

async function loadSessions() {
    try {
        const response = await apiCall('/chat/sessions');
        if (!response) return;

        const data = await response.json();
        const sessionList = document.getElementById('sessionList');
        sessionList.innerHTML = '';

        if (data.sessions && data.sessions.length > 0) {
            const hasCurrentSession = data.sessions.some((session) => Number(session.session_id) === Number(currentSessionId));
            if (!currentSessionId || !hasCurrentSession) {
                currentSessionId = data.sessions[0].session_id;
            }

            data.sessions.forEach((session) => {
                sessionList.appendChild(createSessionItem(session));
            });

            await loadSessionMessages(currentSessionId);
        } else {
            currentSessionId = null;
            currentSessionHasMessages = false;
            sessionList.innerHTML = '<div class="empty-sessions">鏆傛棤浼氳瘽</div>';
            renderEmptyChat();
        }
    } catch (error) {
        console.error('鍔犺浇浼氳瘽澶辫触:', error);
        showMessageNotice('鍔犺浇浼氳瘽澶辫触', 'error');
    }
}

async function createNewSession() {
    if (isSendingMessage) {
        showMessageNotice('消息发送中，请稍后再新建会话', 'error');
        return;
    }

    try {
        await deleteEmptyCurrentSession();

        const response = await apiCall('/chat/session', {
            method: 'POST',
            body: JSON.stringify({ title: '新会话' })
        });
        if (!response) return;

        const data = await response.json();
        if (data.session_id) {
            currentSessionId = data.session_id;
            currentSessionHasMessages = false;
            renderEmptyChat();
            clearMessageNotice();
            renderActiveSessionStub(data.session_id);
        }
    } catch (error) {
        console.error('鍒涘缓浼氳瘽澶辫触:', error);
        showMessageNotice('鍒涘缓浼氳瘽澶辫触', 'error');
    }
}

async function loadSessionMessages(sessionId) {
    if (isSendingMessage && Number(currentSessionId) !== Number(sessionId)) {
        showMessageNotice('消息发送中，请稍后再切换会话', 'error');
        return;
    }

    await deleteEmptyCurrentSession(sessionId);
    currentSessionId = Number(sessionId);
    clearMessageNotice();

    document.querySelectorAll('.session-item').forEach((item) => {
        item.classList.remove('active');
        if (parseInt(item.dataset.sessionId, 10) === Number(sessionId)) {
            item.classList.add('active');
        }
    });

    try {
        const response = await apiCall(`/chat/messages?session_id=${encodeURIComponent(sessionId)}`);
        if (!response) return;

        const data = await response.json();
        if (!response.ok || !data.success) {
            showMessageNotice(data.message || '鍔犺浇娑堟伅澶辫触', 'error');
            currentSessionId = null;
            currentSessionHasMessages = false;
            await loadSessions();
            return;
        }

        const messagesContainer = document.getElementById('messages');
        messagesContainer.innerHTML = '';

        if (data.messages && data.messages.length > 0) {
            currentSessionHasMessages = true;
            data.messages.forEach((msg) => {
                messagesContainer.appendChild(
                    createMessageGroup(msg.role, msg.content, msg.message_id, msg)
                );
            });
        } else {
            currentSessionHasMessages = false;
            renderEmptyChat();
        }

        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    } catch (error) {
        console.error('鍔犺浇娑堟伅澶辫触:', error);
    }
}

function parseSseBuffer(buffer, onEvent) {
    const normalized = buffer.replace(/\r\n/g, '\n');
    const blocks = normalized.split('\n\n');
    const rest = blocks.pop() || '';

    blocks.forEach((block) => {
        let eventName = 'message';
        const dataLines = [];

        block.split('\n').forEach((line) => {
            if (line.startsWith('event:')) {
                eventName = line.slice(6).trim();
            } else if (line.startsWith('data:')) {
                dataLines.push(line.slice(5).trimStart());
            }
        });

        if (!dataLines.length) return;

        const rawData = dataLines.join('\n');
        try {
            onEvent(eventName, JSON.parse(rawData));
        } catch (error) {
            onEvent(eventName, { type: eventName, content: rawData });
        }
    });

    return rest;
}

async function consumeChatStream(response, aiMsgDiv, aiGroup) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const messagesContainer = document.getElementById('messages');
    let fullReply = '';
    let buffer = '';
    let hasError = false;
    let renderScheduled = false;
    let renderVersion = 0;

    const scheduleStreamingRender = () => {
        if (renderScheduled) return;
        renderScheduled = true;
        const scheduledVersion = renderVersion;
        requestAnimationFrame(() => {
            renderScheduled = false;
            if (scheduledVersion !== renderVersion) return;
            renderStreamingMarkdownContent(fullReply, aiMsgDiv);
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        });
    };

    const handleEvent = (eventName, payload) => {
        const type = payload.type || eventName;

        if (type === 'delta') {
            fullReply += payload.content || '';
            scheduleStreamingRender();
        } else if (type === 'usage') {
            attachTokenUsage(aiGroup, payload.usage);
        } else if (type === 'error') {
            hasError = true;
            renderVersion += 1;
            aiMsgDiv.textContent = payload.message || payload.content || '璇锋眰澶辫触锛岃绋嶅悗鍐嶈瘯';
        }

        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    };

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            buffer = parseSseBuffer(buffer, handleEvent);
        }

        const trailingChunk = decoder.decode();
        if (trailingChunk) {
            buffer += trailingChunk;
        }
        if (buffer.trim()) {
            parseSseBuffer(`${buffer}\n\n`, handleEvent);
        }
    } catch (error) {
        if (error.name !== 'AbortError') {
            throw error;
        }
    }

    if (fullReply && !hasError) {
        renderVersion += 1;
        renderScheduled = false;
        renderMarkdownContent(fullReply, aiMsgDiv, false);
    }

    return fullReply;
}

async function stopGeneration() {
    if (!isSendingMessage || !currentAbortController || isStopping) {
        return;
    }

    const sessionId = currentSessionId;
    if (!sessionId) {
        return;
    }

    isStopping = true;
    const btn = document.getElementById('sendBtn');
    if (btn) btn.disabled = true;

    try {
        await apiCall(`/chat/stream/${sessionId}/stop`, { method: 'POST' });
        showMessageNotice('宸插仠姝㈢敓鎴愶紝姝ｅ湪鏀跺熬...', 'info');
    } catch (error) {
        if (error.name !== 'AbortError') {
            console.error('鍋滄鐢熸垚澶辫触:', error);
        }
        if (currentAbortController) {
            currentAbortController.abort();
        }
        return;
    }}

async function sendMessage() {
    if (isSendingMessage) return;

    const input = document.getElementById('messageInput');
    const userMessage = input.value.trim();

    if (!userMessage) {
        showMessageNotice('请输入消息内容', 'error');
        return;
    }

    if (!currentSessionId) {
        showMessageNotice('请先创建或选择一个会话', 'error');
        return;
    }

    if (editingMessageId) {
        const messageId = editingMessageId;
        editingMessageId = null;
        input.value = '';
        await modifyMessageStream(messageId, userMessage);
        return;
    }

    isSendingMessage = true;
    setLoadingState(true);
    clearMessageNotice();
    input.value = '';

    const messagesContainer = document.getElementById('messages');
    const emptyState = messagesContainer.querySelector('.empty-state');
    if (emptyState) {
        messagesContainer.innerHTML = '';
    }

    const userMsgDiv = document.createElement('div');
    userMsgDiv.className = 'message user';
    userMsgDiv.textContent = userMessage;
    messagesContainer.appendChild(userMsgDiv);

    const aiGroup = document.createElement('div');
    aiGroup.className = 'message-group assistant';

    const aiMsgDiv = document.createElement('div');
    aiMsgDiv.className = 'message assistant';
    aiMsgDiv.textContent = '姝ｅ湪鎬濊€?..';
    aiGroup.appendChild(aiMsgDiv);
    messagesContainer.appendChild(aiGroup);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    currentAbortController = new AbortController();

    try {
        const response = await apiCall('/chat/stream', {
            method: 'POST',
            body: JSON.stringify({
                session_id: currentSessionId,
                message: userMessage
            }),
            signal: currentAbortController.signal
        });

        if (!response) {
            if (!currentAbortController || currentAbortController.signal.aborted) {
                return;
            }
            aiMsgDiv.textContent = '璇锋眰澶辫触锛岃绋嶅悗鍐嶈瘯';
            return;
        }

        if (!response.body) {
            aiMsgDiv.textContent = '鍝嶅簲寮傚父锛岃绋嶅悗鍐嶈瘯';
            return;
        }

        await consumeChatStream(response, aiMsgDiv, aiGroup);

        currentSessionHasMessages = true;
        await loadSessions();
    } catch (error) {
        if (error.name === 'AbortError') {
            return;
        }
        console.error('鍙戦€佹秷鎭け璐?', error);
        aiMsgDiv.textContent = '发送消息失败，请稍后再试';
    } finally {
        currentAbortController = null;
        isStopping = false;
        isSendingMessage = false;
        setLoadingState(false);
    }
}

async function modifyMessageStream(messageId, newContent) {
    isSendingMessage = true;
    setLoadingState(true);
    clearMessageNotice();

    const messagesContainer = document.getElementById('messages');
    const messageGroup = messagesContainer.querySelector(`.message-group[data-message-id="${messageId}"]`);

    if (messageGroup) {
        const messageContent = messageGroup.querySelector('.message');
        if (messageContent) {
            messageContent.textContent = newContent;
        }

        while (messageGroup.nextElementSibling) {
            messageGroup.nextElementSibling.remove();
        }
    } else {
        messagesContainer.innerHTML = '';
        messagesContainer.appendChild(createMessageGroup('user', newContent, messageId));
    }

    const aiGroup = document.createElement('div');
    aiGroup.className = 'message-group assistant';

    const aiMsgDiv = document.createElement('div');
    aiMsgDiv.className = 'message assistant';
    aiMsgDiv.textContent = '姝ｅ湪淇敼骞堕噸鏂扮敓鎴愬洖澶?..';
    aiGroup.appendChild(aiMsgDiv);
    messagesContainer.appendChild(aiGroup);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    currentAbortController = new AbortController();

    try {
        const response = await apiCall(
            `/chat/messages/${messageId}/stream`,
            {
                method: 'PUT',
                body: JSON.stringify({ content: newContent }),
                signal: currentAbortController.signal
            }
        );
        if (!response) {
            if (!currentAbortController || currentAbortController.signal.aborted) {
                return;
            }
            return;
        }

        if (!response.ok) {
            const errorText = await response.text().catch(() => '');
            aiMsgDiv.textContent = errorText || '淇敼娑堟伅澶辫触';
            showMessageNotice('淇敼娑堟伅澶辫触', 'error');
            return;
        }

        if (!response.body) {
            aiMsgDiv.textContent = '鍝嶅簲寮傚父锛岃绋嶅悗鍐嶈瘯';
            showMessageNotice('淇敼娑堟伅澶辫触', 'error');
            return;
        }

        await consumeChatStream(response, aiMsgDiv, aiGroup);

        currentSessionHasMessages = true;
        showMessageNotice('淇敼鎴愬姛', 'success');
        await loadSessions();
    } catch (error) {
        if (error.name === 'AbortError') {
            return;
        }
        console.error('淇敼娑堟伅澶辫触:', error);
        aiMsgDiv.textContent = '淇敼娑堟伅澶辫触';
        showMessageNotice('淇敼娑堟伅澶辫触', 'error');
    } finally {
        currentAbortController = null;
        isStopping = false;
        isSendingMessage = false;
        setLoadingState(false);
    }
}

function showAdminNotice(message, type = 'info') {
    const notice = document.getElementById('adminNotice');
    if (!notice) return;
    notice.textContent = message;
    notice.className = `notice ${type}`;
}

function clearAdminNotice() {
    showAdminNotice('', 'info');
}

function getSelectedRoleId() {
    const select = document.getElementById('rolePermissionSelect');
    return select ? Number(select.value) : 0;
}

function renderAdminStats() {
    const dashboard = adminData.dashboard || {};
    const stats = [
        { id: 'adminStatUsers', value: dashboard.users != null ? dashboard.users : adminData.users.length },
        { id: 'adminStatRoles', value: dashboard.roles != null ? dashboard.roles : adminData.roles.length },
        { id: 'adminStatPermissions', value: dashboard.permissions != null ? dashboard.permissions : adminData.permissions.length },
        { id: 'adminStatAdmins', value: dashboard.admin_users != null ? dashboard.admin_users : 0 },
    ];
    stats.forEach(({ id, value }) => {
        const node = document.getElementById(id);
        if (node) node.textContent = String(value);
    });
}

function renderRoleSelectOptions() {
    const select = document.getElementById('rolePermissionSelect');
    if (!select) return;
    const current = select.value;
    select.innerHTML = adminData.roles.map((role) => (
        `<option value="${role.role_id}">${role.name}</option>`
    )).join('');
    if (current) {
        select.value = current;
    }
}

function renderPermissionChecklist(selectedCodes = []) {
    const container = document.getElementById('rolePermissionChecklist');
    if (!container) return;
    const selected = new Set(selectedCodes);
    container.innerHTML = adminData.permissions.map((permission) => `
        <label class="check-row">
            <input type="checkbox" value="${permission.code}" ${selected.has(permission.code) ? 'checked' : ''}>
            <span>
                <strong>${permission.code}</strong>
                <em>${permission.name}</em>
            </span>
        </label>
    `).join('');
}

function renderAdminUsers() {
    const tbody = document.getElementById('adminUsersBody');
    if (!tbody) return;
    const roles = adminData.roles || [];
    tbody.innerHTML = adminData.users.map((user) => {
        const roleOptions = roles.map((role) => (
            `<option value="${role.role_id}" ${role.name === user.role ? 'selected' : ''}>${role.name}</option>`
        )).join('');
        const permissionText = (user.permissions || []).join(', ') || '--';
        return `
            <tr data-user-id="${user.user_id}">
                <td>${user.username}</td>
                <td>${user.role}</td>
                <td>${permissionText}</td>
                <td>
                    <select class="inline-select user-role-select">
                        ${roleOptions}
                    </select>
                </td>
                <td>
                    <button type="button" class="secondary-action user-role-save">淇濆瓨</button>
                </td>
            </tr>
        `;
    }).join('');

    tbody.querySelectorAll('.user-role-save').forEach((button) => {
        button.addEventListener('click', async () => {
            const row = button.closest('tr');
            if (!row) return;
            const userId = Number(row.dataset.userId);
            const roleSelect = row.querySelector('.user-role-select');
            if (!roleSelect) return;
            await updateUserRole(userId, Number(roleSelect.value));
        });
    });
}

function renderAdminRoles() {
    const tbody = document.getElementById('adminRolesBody');
    if (!tbody) return;
    tbody.innerHTML = adminData.roles.map((role) => `
        <tr data-role-id="${role.role_id}">
            <td>${role.name}</td>
            <td>${role.description || '--'}</td>
            <td>${(role.permissions || []).map((item) => item.code || item).join(', ') || '--'}</td>
            <td>
                <button type="button" class="secondary-action role-edit-btn">缂栬緫鏉冮檺</button>
            </td>
        </tr>
    `).join('');

    tbody.querySelectorAll('.role-edit-btn').forEach((button) => {
        button.addEventListener('click', () => {
            const row = button.closest('tr');
            if (!row) return;
            const roleId = Number(row.dataset.roleId);
            selectRoleForEdit(roleId);
        });
    });
}

function renderAdminPermissions() {
    const tbody = document.getElementById('adminPermissionsBody');
    if (!tbody) return;
    tbody.innerHTML = adminData.permissions.map((permission) => `
        <tr>
            <td>${permission.code}</td>
            <td>${permission.name}</td>
            <td>${permission.description || '--'}</td>
        </tr>
    `).join('');
}

function populateRoleForm(roleId) {
    const role = adminData.roles.find((item) => Number(item.role_id) === Number(roleId));
    const title = document.getElementById('roleEditorTitle');
    const name = document.getElementById('roleNamePreview');
    if (!role) return;
    if (title) title.textContent = `瑙掕壊鏉冮檺 - ${role.name}`;
    if (name) name.textContent = role.name;
    const selectedCodes = (role.permissions || []).map((item) => item.code || item);
    renderPermissionChecklist(selectedCodes);
    const select = document.getElementById('rolePermissionSelect');
    if (select) select.value = String(role.role_id);
    var rolePermissionSaveBtn = document.getElementById('rolePermissionSaveBtn');
    if (rolePermissionSaveBtn) {
        rolePermissionSaveBtn.setAttribute('data-role-id', String(role.role_id));
    }
}

function selectRoleForEdit(roleId) {
    populateRoleForm(roleId);
}

async function loadAdminDashboard() {
    if (!currentUser || currentUser.role !== 'admin') {
        return;
    }

    clearAdminNotice();
    try {
        const [dashboardRes, usersRes, rolesRes, permissionsRes] = await Promise.all([
            apiCall('/admin/dashboard'),
            apiCall('/admin/users'),
            apiCall('/admin/roles'),
            apiCall('/admin/permissions'),
        ]);

        const dashboard = dashboardRes ? await dashboardRes.json() : null;
        const users = usersRes ? await usersRes.json() : [];
        const roles = rolesRes ? await rolesRes.json() : [];
        const permissions = permissionsRes ? await permissionsRes.json() : [];

        adminData = {
            dashboard: dashboard && dashboard.success ? dashboard.summary : null,
            users: Array.isArray(users) ? users : [],
            roles: Array.isArray(roles) ? roles : [],
            permissions: Array.isArray(permissions) ? permissions : [],
        };

        renderAdminStats();
        renderRoleSelectOptions();
        renderAdminUsers();
        renderAdminRoles();
        renderAdminPermissions();

        const initialRoleId = adminData.roles[0] ? adminData.roles[0].role_id : 0;
        if (initialRoleId) {
            populateRoleForm(initialRoleId);
        }
    } catch (error) {
        console.error('loadAdminDashboard failed:', error);
        showAdminNotice('加载管理员数据失败', 'error');
    }
}

async function updateUserRole(userId, roleId) {
    try {
        const response = await apiCall(`/admin/users/${userId}/role`, {
            method: 'PATCH',
            body: JSON.stringify({ role_id: roleId }),
        });
        if (!response) return;

        const data = await response.json();
        if (!response.ok || !data.success) {
            showAdminNotice(data.message || '鏇存柊鐢ㄦ埛瑙掕壊澶辫触', 'error');
            return;
        }

        showAdminNotice('用户角色已更新', 'success');
        await loadAdminDashboard();
    } catch (error) {
        console.error('updateUserRole failed:', error);
        showAdminNotice('鏇存柊鐢ㄦ埛瑙掕壊澶辫触', 'error');
    }
}

async function saveSelectedRolePermissions() {
    const roleId = getSelectedRoleId();
    if (!roleId) {
        showAdminNotice('请先选择一个角色', 'error');
        return;
    }

    const checkboxes = Array.from(document.querySelectorAll('#rolePermissionChecklist input[type="checkbox"]'));
    const permissionCodes = checkboxes.filter((item) => item.checked).map((item) => item.value);

    try {
        const response = await apiCall(`/admin/roles/${roleId}/permissions`, {
            method: 'PUT',
            body: JSON.stringify({ permission_codes: permissionCodes }),
        });
        if (!response) return;

        const data = await response.json();
        if (!response.ok || !data.success) {
            showAdminNotice(data.message || '鏇存柊瑙掕壊鏉冮檺澶辫触', 'error');
            return;
        }

        showAdminNotice('角色权限已更新', 'success');
        await loadAdminDashboard();
    } catch (error) {
        console.error('saveSelectedRolePermissions failed:', error);
        showAdminNotice('鏇存柊瑙掕壊鏉冮檺澶辫触', 'error');
    }
}

async function createNewRole() {
    const nameInput = document.getElementById('roleNameInput');
    const descriptionInput = document.getElementById('roleDescriptionInput');
    const name = nameInput ? nameInput.value.trim() : '';
    const description = descriptionInput ? descriptionInput.value.trim() : '';

    if (!name) {
        showAdminNotice('请输入角色名称', 'error');
        return;
    }

    try {
        const response = await apiCall('/admin/roles', {
            method: 'POST',
            body: JSON.stringify({ name, description: description || null }),
        });
        if (!response) return;

        const data = await response.json();
        if (!response.ok || !data.role_id) {
            showAdminNotice(data.message || '鍒涘缓瑙掕壊澶辫触', 'error');
            return;
        }

        if (nameInput) nameInput.value = '';
        showAdminNotice('角色已创建', 'success');
        await loadAdminDashboard();
        populateRoleForm(data.role_id);
    } catch (error) {
        console.error('createNewRole failed:', error);
        showAdminNotice('鍒涘缓瑙掕壊澶辫触', 'error');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    if (isLoggedIn()) {
        showMainPage();
    } else {
        showLoginPage();
    }

    document.getElementById('loginTab').addEventListener('click', () => {
        document.getElementById('loginForm').style.display = 'block';
        document.getElementById('registerForm').style.display = 'none';
        document.getElementById('loginTab').classList.add('active');
        document.getElementById('registerTab').classList.remove('active');
    });

    document.getElementById('registerTab').addEventListener('click', () => {
        document.getElementById('loginForm').style.display = 'none';
        document.getElementById('registerForm').style.display = 'block';
        document.getElementById('registerTab').classList.add('active');
        document.getElementById('loginTab').classList.remove('active');
    });

    document.getElementById('registerBtn').addEventListener('click', handleRegister);
    document.getElementById('loginBtn').addEventListener('click', handleLogin);
    document.getElementById('logoutBtn').addEventListener('click', handleLogout);
    document.getElementById('refreshCaptchaBtn').addEventListener('click', loadCaptcha);
    document.getElementById('newChatBtn').addEventListener('click', createNewSession);
    document.getElementById('sendBtn').addEventListener('click', () => {
        const btn = document.getElementById('sendBtn');
        if (btn.classList.contains('stop-mode')) {
            stopGeneration();
        } else {
            sendMessage();
        }
    });
    document.getElementById('saveSettingsBtn').addEventListener('click', saveSettings);
    document.getElementById('profileBtn').addEventListener('click', showProfilePage);
    document.getElementById('backBtn').addEventListener('click', showMainPage);
    document.getElementById('toggleApiKeyBtn').addEventListener('click', toggleApiKeyVisibility);
    const adminBtn = document.getElementById('adminBtn');
    if (adminBtn) {
        adminBtn.addEventListener('click', showAdminView);
    }
    const backToChatBtn = document.getElementById('backToChatBtn');
    if (backToChatBtn) {
        backToChatBtn.addEventListener('click', showChatView);
    }
    const refreshAdminBtn = document.getElementById('refreshAdminBtn');
    if (refreshAdminBtn) {
        refreshAdminBtn.addEventListener('click', loadAdminDashboard);
    }
    const createRoleBtn = document.getElementById('createRoleBtn');
    if (createRoleBtn) {
        createRoleBtn.addEventListener('click', createNewRole);
    }
    const saveRolePermissionsBtn = document.getElementById('rolePermissionSaveBtn');
    if (saveRolePermissionsBtn) {
        saveRolePermissionsBtn.addEventListener('click', saveSelectedRolePermissions);
    }
    const rolePermissionSelect = document.getElementById('rolePermissionSelect');
    if (rolePermissionSelect) {
        rolePermissionSelect.addEventListener('change', (event) => {
            selectRoleForEdit(Number(event.target.value));
        });
    }

    document.querySelectorAll('.theme-btn').forEach((btn) => {
        btn.addEventListener('click', () => setTheme(btn.dataset.theme));
    });

    document.getElementById('messageInput').addEventListener('keypress', (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            sendMessage();
        }
    });

    updateSidebarState();
});










