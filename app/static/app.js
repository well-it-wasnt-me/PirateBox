/**
 * PirateBox chat UI glue code.
 * It keeps the messages moving while the internet takes the day off.
 */
(function () {
  const chatList = document.getElementById("chat-messages");
  if (!chatList) {
    return;
  }

  const chatForm = document.getElementById("chat-form");
  const nicknameInput = document.getElementById("chat-nickname");
  const messageInput = document.getElementById("chat-message");
  const storageKey = "piratebox.nickname";

  let lastId = parseInt(chatList.dataset.lastId || "0", 10);

  if (nicknameInput) {
    const stored = window.localStorage.getItem(storageKey);
    if (stored) {
      nicknameInput.value = stored;
    }
  }

  /**
   * Persist the nickname locally so users can pretend it's an identity.
   */
  function rememberNickname() {
    if (!nicknameInput) {
      return;
    }
    const value = nicknameInput.value.trim();
    if (value) {
      window.localStorage.setItem(storageKey, value);
    }
  }

  /**
   * Render a single chat message into the DOM.
   * @param {{id:number, nickname:string, message:string, created_at:string}} msg
   */
  function appendMessage(msg) {
    const item = document.createElement("li");

    const nick = document.createElement("span");
    nick.className = "chat-nick";
    nick.textContent = msg.nickname || "Anonymous";

    const time = document.createElement("span");
    time.className = "chat-time";
    time.textContent = msg.created_at.replace("T", " ").replace("+00:00", " UTC");

    const text = document.createElement("div");
    text.className = "chat-text";
    text.textContent = msg.message;

    item.appendChild(nick);
    item.appendChild(time);
    item.appendChild(text);
    chatList.appendChild(item);
  }

  /**
   * Scroll the chat list to the latest message.
   */
  function scrollToBottom() {
    chatList.parentElement.scrollTop = chatList.parentElement.scrollHeight;
  }

  /**
   * Poll the server for new chat messages.
   */
  async function fetchMessages() {
    try {
      const response = await fetch(`/api/chat/messages?after_id=${lastId}`);
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      if (!payload.messages || payload.messages.length === 0) {
        return;
      }
      payload.messages.forEach((msg) => {
        appendMessage(msg);
        lastId = Math.max(lastId, msg.id);
      });
      scrollToBottom();
    } catch (err) {
      // network hiccups happen; the void does not care.
    }
  }

  /**
   * Send the chat message form without a page reload.
   * @param {Event} evt
   */
  async function sendMessage(evt) {
    evt.preventDefault();
    if (!messageInput) {
      return;
    }
    const message = messageInput.value.trim();
    if (!message) {
      return;
    }
    rememberNickname();

    const body = new URLSearchParams();
    if (nicknameInput && nicknameInput.value.trim()) {
      body.set("nickname", nicknameInput.value.trim());
    }
    body.set("message", message);

    try {
      const response = await fetch("/api/chat/messages", {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body,
      });
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      if (payload.message) {
        appendMessage(payload.message);
        lastId = Math.max(lastId, payload.message.id);
        scrollToBottom();
      }
      messageInput.value = "";
    } catch (err) {
      // if it fails, it fails. no tears.
    }
  }

  fetchMessages();
  window.setInterval(fetchMessages, 2500);
  if (chatForm) {
    chatForm.addEventListener("submit", sendMessage);
  }
})();
