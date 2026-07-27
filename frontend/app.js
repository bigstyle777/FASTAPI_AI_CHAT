const API_BASE_URL = "http://127.0.0.1:8000";

let currentSessionId = null;
let isSendingMessage = false;
let currentCaptchaId = null;

function getToken() {
    return localStorage.getItem('token');
}

function setToken(token) {
    localStorage.setItem('token', token);
}

function removeToken() {
    localStorage.removeItem('token');
}

function clearAuthState() {
    removeToken();
    currentSessionId = null;
    showLoginPage();
}

function isLoggedIn() {
    return !!getToken();
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
    document.getElementById('mainPage').style.display = 'none';
    document.getElementById('profilePage').style.display = 'flex';
    loadProfile();
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

async function apiCall(url, options = {}, requestOptions = {}) {
    const { auth = true, handleUnauthorized = true } = requestOptions;
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };

    const token = auth ? getToken() : null;
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
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
        showMessageNotice('网络请求失败，请检查后端服务是否运行', 'error');
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
            showMessageNotice(data.message || '验证码加载失败，请确认 Redis 服务已启动', 'error');
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
        if (apiKeyInput) {
            apiKeyInput.value = data.api_key || '';
        }
        const providerSelect = document.getElementById('providerSelect');
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
        if (data.success) {
            showMessageNotice('设置已保存', 'success');
        } else {
            showMessageNotice('保存设置失败', 'error');
        }
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
        });

        if (!response) {
            return;
        }

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
        });

        if (!response) {
            return;
        }

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

async function loadSessions() {
    try {
        const response = await apiCall('/chat/sessions');
        if (!response) return;

        const data = await response.json();
        const sessionList = document.getElementById('sessionList');
        sessionList.innerHTML = '';

        if (data.sessions && data.sessions.length > 0) {
            data.sessions.forEach((session) => {
                const sessionItem = document.createElement('div');
                sessionItem.className = 'session-item';
                sessionItem.dataset.sessionId = session.session_id;
                sessionItem.innerHTML = `
                    <div class="session-title">${session.title || '新会话'}</div>
                    <div class="session-last-message">${session.last_message || '暂无消息'}</div>
                `;
                sessionItem.onclick = () => loadSessionMessages(session.session_id);
                sessionList.appendChild(sessionItem);

                if (!currentSessionId) {
                    currentSessionId = session.session_id;
                    loadSessionMessages(session.session_id);
                }
            });
        } else {
            sessionList.innerHTML = '<div class="empty-sessions">暂无会话</div>';
            renderEmptyChat();
        }
    } catch (error) {
        console.error('加载会话失败:', error);
        showMessageNotice('加载会话失败', 'error');
    }
}

async function createNewSession() {
    try {
        const response = await apiCall('/chat/session', {
            method: 'POST',
            body: JSON.stringify({ title: '新会话' })
        });

        if (!response) return;

        const data = await response.json();
        if (data.session_id) {
            currentSessionId = data.session_id;
            renderEmptyChat();
            clearMessageNotice();
            loadSessions();
        }
    } catch (error) {
        console.error('创建会话失败:', error);
        showMessageNotice('创建会话失败', 'error');
    }
}

async function loadSessionMessages(sessionId) {
    currentSessionId = sessionId;
    clearMessageNotice();

    document.querySelectorAll('.session-item').forEach((item) => {
        item.classList.remove('active');
        if (parseInt(item.dataset.sessionId, 10) === sessionId) {
            item.classList.add('active');
        }
    });

    try {
        const response = await apiCall(`/chat/messages?session_id=${sessionId}`);
        if (!response) return;

        const data = await response.json();
        const messagesContainer = document.getElementById('messages');
        messagesContainer.innerHTML = '';

        if (data.messages && data.messages.length > 0) {
            data.messages.forEach((msg) => {
                const msgDiv = document.createElement('div');
                msgDiv.className = `message ${msg.role}`;
                msgDiv.textContent = msg.content;
                messagesContainer.appendChild(msgDiv);
            });
        } else {
            renderEmptyChat();
        }

        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    } catch (error) {
        console.error('加载消息失败:', error);
    }
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

    const aiMsgDiv = document.createElement('div');
    aiMsgDiv.className = 'message assistant';
    aiMsgDiv.textContent = '正在思考...';
    messagesContainer.appendChild(aiMsgDiv);
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

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let fullReply = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value);
            fullReply += chunk;
            aiMsgDiv.textContent = fullReply;
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }

        loadSessions();
    } catch (error) {
        console.error('发送消息失败:', error);
        aiMsgDiv.textContent = '发送消息失败，请稍后再试';
    } finally {
        isSendingMessage = false;
        setLoadingState(false);
    }
}

document.addEventListener('DOMContentLoaded', () => {
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

    document.getElementById('messageInput').addEventListener('keypress', (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            sendMessage();
        }
    });
});
