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
  let fetching = false;
  let fetchAgain = false;

  /**
   * Read localStorage without exploding in private mode, where storage is a rumor.
   * @param {string} key
   * @returns {string|null}
   */
  function readStorage(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (err) {
      return null;
    }
  }

  if (nicknameInput) {
    const stored = readStorage(storageKey);
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
    if (!value) {
      return;
    }
    try {
      window.localStorage.setItem(storageKey, value);
    } catch (err) {
      // the browser said no; the message still goes out, the identity does not.
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
   * One request at a time: overlapping polls on a slow Pi love printing everything twice.
   */
  async function fetchMessages() {
    if (fetching) {
      fetchAgain = true;
      return;
    }
    fetching = true;
    try {
      const response = await fetch(`/api/chat/messages?after_id=${lastId}`);
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      if (!payload.messages || payload.messages.length === 0) {
        return;
      }
      let added = false;
      payload.messages.forEach((msg) => {
        if (msg.id <= lastId) {
          return;
        }
        appendMessage(msg);
        lastId = msg.id;
        added = true;
      });
      if (added) {
        scrollToBottom();
      }
    } catch (err) {
      // network hiccups happen; the void does not care.
    } finally {
      fetching = false;
      if (fetchAgain) {
        fetchAgain = false;
        fetchMessages();
      }
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
      messageInput.value = "";
      // Let the poller render it, in order, exactly once. Novel concept.
      fetchMessages();
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
