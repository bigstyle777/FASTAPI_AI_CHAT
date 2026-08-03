const API_BASE_URL = "http://127.0.0.1:8000";
const THEME_STORAGE_KEY = 'aichatpro-theme';
const VALID_THEMES = ['light', 'dark', 'system'];
let systemThemeMedia = window.matchMedia('(prefers-color-scheme: dark)');

let currentSessionId = null;
let isSendingMessage = false;
let currentCaptchaId = null;
let currentSessionHasMessages = false;
let editingMessageId = null;

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
        loginBtn: '登录',
        registerBtn: '注册',
    };

    button.disabled = isLoading;
    button.textContent = isLoading ? '处理中...' : (defaultText[buttonId] || '确定');
}

function renderEmptyChat() {
    const messagesContainer = document.getElementById('messages');
    messagesContainer.innerHTML = `
        <div class="empty-state">
            <div class="empty-icon">AI</div>
            <h2>开始一次新的对话</h2>
            <p>选择左侧会话，或新建聊天后输入你的问题。</p>
        </div>
    `;
}

function showLoginPage() {
    document.getElementById('loginPage').style.display = 'flex';
    document.getElementById('mainPage').style.display = 'none';
    document.getElementById('profilePage').style.display = 'none';
    loadCaptcha();
}

function showMainPage() {
    document.getElementById('loginPage').style.display = 'none';
    document.getElementById('mainPage').style.display = 'flex';
    document.getElementById('profilePage').style.display = 'none';
    loadSessions();
    loadSettings();
}

function showProfilePage() {
    document.getElementById('loginPage').style.display = 'none';
    document.getElementById('mainPage').style.display = 'none';
    document.getElementById('profilePage').style.display = 'flex';
    loadProfile();
}

function clearAuthState() {
    removeToken();
    currentSessionId = null;
    currentSessionHasMessages = false;
    showLoginPage();
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
        console.error('网络请求失败:', error);
        showMessageNotice('网络请求失败，请检查服务器是否启动', 'error');
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
        console.error('加载验证码失败:', error);
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
        console.error('加载设置失败:', error);
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
        console.error('加载个人信息失败:', error);
    }
}

function toggleApiKeyVisibility() {
    const input = document.getElementById('apiKeyInput');
    const button = document.getElementById('toggleApiKeyBtn');
    if (!input || !button) return;

    if (input.type === 'password') {
        input.type = 'text';
        button.textContent = '隐藏';
    } else {
        input.type = 'password';
        button.textContent = '显示';
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
        console.error('保存设置失败:', error);
        showMessageNotice('保存设置失败', 'error');
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
            showMessageNotice('注册成功，请登录', 'success');
            document.getElementById('registerUsername').value = '';
            document.getElementById('registerPassword').value = '';
            document.getElementById('loginTab').click();
        } else {
            showMessageNotice(data.message || '注册失败', 'error');
        }
    } catch (error) {
        console.error('注册失败:', error);
        showMessageNotice('注册失败，请重试', 'error');
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
            showMessageNotice('登录成功', 'success');
        } else {
            showMessageNotice(data.message || '登录失败', 'error');
            await loadCaptcha();
        }
    } catch (error) {
        console.error('登录失败:', error);
        showMessageNotice('登录失败，请重试', 'error');
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

function openSessionMenu(triggerBtn, sessionId, titleText) {
    closeSessionMenu();
    closeMessageMenu();

    const menu = document.createElement('div');
    menu.className = 'session-menu';

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
    deleteItem.textContent = '删除会话';
    deleteItem.addEventListener('click', (event) => {
        event.stopPropagation();
        closeSessionMenu();
        deleteSession(sessionId);
    });

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

    const menuTrigger = messageGroup.querySelector('.message-menu-trigger');
    const usageElement = createTokenUsageElement(usage);
    if (menuTrigger) {
        messageGroup.insertBefore(usageElement, menuTrigger);
    } else {
        messageGroup.appendChild(usageElement);
    }
}

function createMessageGroup(role, content, messageId, usage = null) {
    const group = document.createElement('div');
    group.className = `message-group ${role}`;
    group.dataset.messageId = messageId;

    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${role}`;
    msgDiv.textContent = content;
    group.appendChild(msgDiv);

    if (role === 'assistant') {
        attachTokenUsage(group, usage);
    }

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'message-menu-trigger';
    trigger.setAttribute('aria-label', '消息操作');
    trigger.innerHTML = '&#8942;';
    trigger.title = '更多操作';
    trigger.addEventListener('click', (event) => {
        event.stopPropagation();
        openMessageMenu(trigger, messageId, role, content);
    });
    group.appendChild(trigger);

    return group;
}

function openMessageMenu(triggerBtn, messageId, role, content) {
    closeMessageMenu();
    closeSessionMenu();

    const menu = document.createElement('div');
    menu.className = 'message-menu';

    if (role === 'user') {
        const modifyItem = document.createElement('button');
        modifyItem.type = 'button';
        modifyItem.className = 'message-menu-item';
        modifyItem.textContent = '修改消息';
        modifyItem.addEventListener('click', (event) => {
            event.stopPropagation();
            closeMessageMenu();
            editingMessageId = messageId;
            const input = document.getElementById('messageInput');
            input.value = content;
            input.focus();
            showMessageNotice('已进入修改该消息状态，发送后将更新原消息，并重新生成回复', 'info');
        });
        menu.appendChild(modifyItem);
    }

    const deleteItem = document.createElement('button');
    deleteItem.type = 'button';
    deleteItem.className = 'message-menu-item danger';
    deleteItem.textContent = '删除消息';
    deleteItem.addEventListener('click', (event) => {
        event.stopPropagation();
        closeMessageMenu();
        deleteMessage(messageId);
    });
    menu.appendChild(deleteItem);

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
        showMessageNotice('无法删除：消息ID缺失', 'error');
        return;
    }

    if (!window.confirm('确定删除这条消息吗？')) {
        return;
    }

    try {
        const response = await apiCall(`/chat/messages/${messageId}`, { method: 'DELETE' });
        if (!response) return;

        const data = await response.json();
        if (!response.ok || !data.success) {
            showMessageNotice(data.message || '删除消息失败', 'error');
            return;
        }

        showMessageNotice(data.message || '消息已删除', 'success');
        await loadSessionMessages(currentSessionId);
    } catch (error) {
        console.error('删除消息失败:', error);
        showMessageNotice('删除消息失败', 'error');
    }
}

function createSessionItem(session) {
    const sessionId = Number(session.session_id);
    const title = session.title || '新会话';
    const lastMessage = session.last_message || '暂无消息';
    const sessionItem = document.createElement('div');
    sessionItem.className = 'session-item';
    sessionItem.dataset.sessionId = sessionId;
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
    menuTrigger.setAttribute('aria-label', '会话操作');
    menuTrigger.innerHTML = '&#8942;';
    menuTrigger.title = '更多操作';
    menuTrigger.addEventListener('click', (event) => {
        event.stopPropagation();
        openSessionMenu(menuTrigger, sessionId, titleElement.textContent);
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
        last_message: '暂无消息'
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
        console.error('自动删除空会话失败:', error);
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
    cancelBtn.textContent = '取消';

    const saveBtn = document.createElement('button');
    saveBtn.type = 'button';
    saveBtn.className = 'modal-btn primary';
    saveBtn.textContent = '保存';

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
            showMessageNotice('会话名称不能为空', 'error');
            input.focus();
            return;
        }
        if (newTitle === currentTitle) {
            closeRenameSessionModal();
            return;
        }

        saveBtn.disabled = true;
        cancelBtn.disabled = true;
        saveBtn.textContent = '保存中...';

        try {
            const response = await apiCall(`/chat/sessions/${sessionId}`, {
                method: 'POST',
                body: JSON.stringify({ title: newTitle })
            });
            if (!response) {
                saveBtn.disabled = false;
                cancelBtn.disabled = false;
                saveBtn.textContent = '保存';
                return;
            }

            const data = await response.json();
            if (!response.ok || !data.success) {
                showMessageNotice(data.message || '修改会话名称失败', 'error');
                saveBtn.disabled = false;
                cancelBtn.disabled = false;
                saveBtn.textContent = '保存';
                return;
            }

            const titleElement = document.querySelector(`.session-item[data-session-id="${sessionId}"] .session-title`);
            if (titleElement) {
                titleElement.textContent = newTitle;
            }
            closeRenameSessionModal();
            showMessageNotice('会话名称已更新', 'success');
        } catch (error) {
            console.error('修改会话名称失败:', error);
            showMessageNotice('修改会话名称失败', 'error');
            saveBtn.disabled = false;
            cancelBtn.disabled = false;
            saveBtn.textContent = '保存';
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
            showMessageNotice(data.message || '删除会话失败', 'error');
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
        console.error('删除会话失败:', error);
        showMessageNotice('删除会话失败', 'error');
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
            sessionList.innerHTML = '<div class="empty-sessions">暂无会话</div>';
            renderEmptyChat();
        }
    } catch (error) {
        console.error('加载会话失败:', error);
        showMessageNotice('加载会话失败', 'error');
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
        console.error('创建会话失败:', error);
        showMessageNotice('创建会话失败', 'error');
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
            showMessageNotice(data.message || '加载消息失败', 'error');
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
        console.error('加载消息失败:', error);
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

    const handleEvent = (eventName, payload) => {
        const type = payload.type || eventName;

        if (type === 'delta') {
            fullReply += payload.content || '';
            aiMsgDiv.textContent = fullReply;
        } else if (type === 'usage') {
            attachTokenUsage(aiGroup, payload.usage);
        } else if (type === 'error') {
            aiMsgDiv.textContent = payload.message || payload.content || '请求失败，请稍后再试';
        }

        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    };

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

    return fullReply;
}

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
    aiMsgDiv.textContent = '正在思考...';
    aiGroup.appendChild(aiMsgDiv);
    messagesContainer.appendChild(aiGroup);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    try {
        const response = await apiCall('/chat/stream', {
            method: 'POST',
            body: JSON.stringify({
                session_id: currentSessionId,
                message: userMessage
            })
        });

        if (!response) {
            aiMsgDiv.textContent = '请求失败，请稍后再试';
            return;
        }

        if (!response.body) {
            aiMsgDiv.textContent = '响应异常，请稍后再试';
            return;
        }

        await consumeChatStream(response, aiMsgDiv, aiGroup);

        currentSessionHasMessages = true;
        await loadSessions();
    } catch (error) {
        console.error('发送消息失败:', error);
        aiMsgDiv.textContent = '发送消息失败，请稍后再试';
    } finally {
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
    aiMsgDiv.textContent = '正在修改并重新生成回复...';
    aiGroup.appendChild(aiMsgDiv);
    messagesContainer.appendChild(aiGroup);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    try {
        const response = await apiCall(
            `/chat/messages/${messageId}/stream`,
            {
                method: 'PUT',
                body: JSON.stringify({ content: newContent })
            }
        );
        if (!response) return;

        if (!response.ok) {
            const errorText = await response.text().catch(() => '');
            aiMsgDiv.textContent = errorText || '修改消息失败';
            showMessageNotice('修改消息失败', 'error');
            return;
        }

        if (!response.body) {
            aiMsgDiv.textContent = '响应异常，请稍后再试';
            showMessageNotice('修改消息失败', 'error');
            return;
        }

        await consumeChatStream(response, aiMsgDiv, aiGroup);

        currentSessionHasMessages = true;
        showMessageNotice('修改成功', 'success');
        await loadSessions();
    } catch (error) {
        console.error('修改消息失败:', error);
        aiMsgDiv.textContent = '修改消息失败';
        showMessageNotice('修改消息失败', 'error');
    } finally {
        isSendingMessage = false;
        setLoadingState(false);
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
    document.getElementById('sendBtn').addEventListener('click', sendMessage);
    document.getElementById('saveSettingsBtn').addEventListener('click', saveSettings);
    document.getElementById('profileBtn').addEventListener('click', showProfilePage);
    document.getElementById('backBtn').addEventListener('click', showMainPage);
    document.getElementById('toggleApiKeyBtn').addEventListener('click', toggleApiKeyVisibility);

    document.querySelectorAll('.theme-btn').forEach((btn) => {
        btn.addEventListener('click', () => setTheme(btn.dataset.theme));
    });

    document.getElementById('messageInput').addEventListener('keypress', (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            sendMessage();
        }
    });
});
