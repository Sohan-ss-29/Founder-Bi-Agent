/**
 * app.js — Founder BI Agent chat interface
 *
 * Handles:
 * - Session management (UUID stored in localStorage)
 * - Sending messages to /api/chat
 * - Rendering assistant responses (with simple markdown)
 * - Sidebar quick actions + chip suggestions
 * - Health check on load
 */

(function () {
  'use strict';

  // ─── State ───────────────────────────────────────────────────────────────────
  let sessionId = localStorage.getItem('bi_agent_session') || '';
  let isLoading = false;

  // ─── DOM refs ─────────────────────────────────────────────────────────────────
  const messageInput = document.getElementById('messageInput');
  const sendBtn = document.getElementById('sendBtn');
  const messagesList = document.getElementById('messagesList');
  const messagesContainer = document.getElementById('messagesContainer');
  const welcomeState = document.getElementById('welcomeState');
  const typingIndicator = document.getElementById('typingIndicator');
  const typingLabel = document.getElementById('typingLabel');
  const statusDot = document.getElementById('statusDot');
  const statusText = document.getElementById('statusText');
  const charCount = document.getElementById('charCount');
  const resetBtn = document.getElementById('resetBtn');
  const sidebar = document.getElementById('sidebar');
  const sidebarToggle = document.getElementById('sidebarToggle');
  const menuBtn = document.getElementById('menuBtn');

  // ─── API base URL ─────────────────────────────────────────────────────────────
  // In production (Render), frontend is served from the same origin as the API.
  const API_BASE = window.location.origin;

  // ─── Health check ─────────────────────────────────────────────────────────────
  async function checkHealth() {
    try {
      const res = await fetch(`${API_BASE}/api/health`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      if (!data.anthropic_key_set) {
        setStatus('error', 'ANTHROPIC_API_KEY not set');
      } else if (!data.monday_token_set) {
        setStatus('error', 'MONDAY_API_TOKEN not set');
      } else {
        setStatus('online', 'Connected');
      }
    } catch (e) {
      setStatus('error', 'API unreachable');
    }
  }

  function setStatus(state, text) {
    statusDot.className = 'status-dot ' + state;
    statusText.textContent = text;
  }

  // ─── Simple markdown renderer ──────────────────────────────────────────────────
  function renderMarkdown(text) {
    // Escape HTML first
    let html = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    // Headers
    html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');

    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Bullet lists (handle -, *, •)
    html = html.replace(/^[\-\*•] (.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>)/s, function (match) {
      return '<ul>' + match + '</ul>';
    });
    // Handle sequential li tags not already wrapped
    html = html.replace(/(<\/li>\n<li>)/g, '</li><li>');

    // Numbered lists
    html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');

    // Horizontal rules
    html = html.replace(/^---+$/gm, '<hr style="border-color: var(--border-subtle); margin: 12px 0;">');

    // Paragraphs: split on double newlines
    const blocks = html.split(/\n\n+/);
    html = blocks.map(block => {
      block = block.trim();
      if (!block) return '';
      if (block.startsWith('<h') || block.startsWith('<ul') || block.startsWith('<ol') || block.startsWith('<hr')) {
        return block;
      }
      // Single newlines within a block → <br>
      return '<p>' + block.replace(/\n/g, '<br>') + '</p>';
    }).join('\n');

    return html;
  }

  // ─── Message rendering ─────────────────────────────────────────────────────────
  function hideWelcome() {
    if (welcomeState && !welcomeState.classList.contains('hidden')) {
      welcomeState.classList.add('hidden');
    }
  }

  function appendMessage(role, content, isError = false) {
    hideWelcome();

    const row = document.createElement('div');
    row.className = `message-row ${role}`;

    let avatarHtml = '';
    if (role === 'assistant') {
      avatarHtml = `
        <div class="agent-avatar">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 2L2 7l10 5 10-5-10-5z"/>
            <path d="M2 17l10 5 10-5"/>
            <path d="M2 12l10 5 10-5"/>
          </svg>
        </div>`;
    } else {
      avatarHtml = `<div class="user-avatar">U</div>`;
    }

    const bubbleClass = isError ? 'message-bubble error' : 'message-bubble';
    const bubbleContent = role === 'user'
      ? `<span class="user-bubble-text">${escapeHtml(content)}</span>`
      : renderMarkdown(content);

    row.innerHTML = `
      ${avatarHtml}
      <div class="${bubbleClass}">${bubbleContent}</div>
    `;

    messagesList.appendChild(row);
    scrollToBottom();
    return row;
  }

  function escapeHtml(text) {
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      messagesContainer.scrollTop = messagesContainer.scrollHeight;
    });
  }

  // ─── Typing indicator ──────────────────────────────────────────────────────────
  const typingMessages = [
    'Thinking...',
    'Querying monday.com...',
    'Analyzing data...',
    'Normalizing records...',
    'Generating insights...',
  ];
  let typingInterval = null;
  let typingMsgIdx = 0;

  function showTyping() {
    typingIndicator.classList.remove('hidden');
    typingMsgIdx = 0;
    typingLabel.textContent = typingMessages[0];
    typingInterval = setInterval(() => {
      typingMsgIdx = (typingMsgIdx + 1) % typingMessages.length;
      typingLabel.textContent = typingMessages[typingMsgIdx];
    }, 2500);
    scrollToBottom();
  }

  function hideTyping() {
    typingIndicator.classList.add('hidden');
    if (typingInterval) {
      clearInterval(typingInterval);
      typingInterval = null;
    }
  }

  // ─── Send message ──────────────────────────────────────────────────────────────
  async function sendMessage(text) {
    text = text.trim();
    if (!text || isLoading) return;

    isLoading = true;
    messageInput.value = '';
    updateInputState();

    appendMessage('user', text);
    showTyping();

    try {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, session_id: sessionId }),
      });

      const data = await res.json();
      hideTyping();

      if (!res.ok) {
        const errMsg = data.detail || `Server error (HTTP ${res.status})`;
        appendMessage('assistant', `⚠️ **Error**: ${errMsg}\n\nPlease check your API keys and try again.`, true);
        return;
      }

      // Save session ID
      if (data.session_id && data.session_id !== sessionId) {
        sessionId = data.session_id;
        localStorage.setItem('bi_agent_session', sessionId);
      }

      appendMessage('assistant', data.response);

    } catch (e) {
      hideTyping();
      appendMessage(
        'assistant',
        `⚠️ **Network Error**: Could not reach the server. Please check your connection and try again.\n\n_${e.message}_`,
        true
      );
    } finally {
      isLoading = false;
      updateInputState();
      messageInput.focus();
    }
  }

  // ─── Input state management ────────────────────────────────────────────────────
  function updateInputState() {
    const hasText = messageInput.value.trim().length > 0;
    sendBtn.disabled = !hasText || isLoading;

    const len = messageInput.value.length;
    const max = 2000;
    if (len > 1600) {
      charCount.textContent = `${len}/${max}`;
      charCount.className = 'char-count ' + (len >= max ? 'at-limit' : 'near-limit');
    } else {
      charCount.textContent = '';
    }

    // Auto-resize textarea
    messageInput.style.height = 'auto';
    messageInput.style.height = Math.min(messageInput.scrollHeight, 200) + 'px';
  }

  // ─── Reset conversation ────────────────────────────────────────────────────────
  async function resetConversation() {
    if (sessionId) {
      try {
        await fetch(`${API_BASE}/api/reset`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: sessionId }),
        });
      } catch (_) { /* ignore */ }
    }
    sessionId = '';
    localStorage.removeItem('bi_agent_session');
    messagesList.innerHTML = '';
    welcomeState.classList.remove('hidden');
    messageInput.value = '';
    updateInputState();
  }

  // ─── Sidebar toggle ────────────────────────────────────────────────────────────
  function toggleSidebar() {
    sidebar.classList.toggle('collapsed');
  }

  // ─── Event listeners ───────────────────────────────────────────────────────────
  messageInput.addEventListener('input', updateInputState);

  messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(messageInput.value);
    }
  });

  sendBtn.addEventListener('click', () => sendMessage(messageInput.value));

  resetBtn.addEventListener('click', resetConversation);
  sidebarToggle.addEventListener('click', toggleSidebar);
  menuBtn.addEventListener('click', toggleSidebar);

  // Quick action buttons in sidebar
  document.querySelectorAll('.quick-action[data-prompt]').forEach(btn => {
    btn.addEventListener('click', () => {
      messageInput.value = btn.dataset.prompt;
      updateInputState();
      // On mobile, close sidebar first
      if (window.innerWidth <= 768) toggleSidebar();
      sendMessage(messageInput.value);
    });
  });

  // Welcome chip buttons
  document.querySelectorAll('.chip[data-prompt]').forEach(chip => {
    chip.addEventListener('click', () => {
      sendMessage(chip.dataset.prompt);
    });
  });

  // ─── Init ─────────────────────────────────────────────────────────────────────
  checkHealth();
  messageInput.focus();
  updateInputState();

  // On mobile, collapse sidebar by default
  if (window.innerWidth <= 768) {
    sidebar.classList.add('collapsed');
  }
})();
