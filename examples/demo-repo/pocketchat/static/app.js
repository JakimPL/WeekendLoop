const messageList = document.getElementById("messages");
const askForm = document.getElementById("ask-form");
const questionInput = document.getElementById("question");
const newChatButton = document.getElementById("new-chat");

function renderMessage(message) {
  const bubble = document.createElement("div");
  bubble.className = `message ${message.author}`;
  bubble.textContent = message.text;
  return bubble;
}

async function showMessagesFrom(response) {
  if (!response.ok) {
    return;
  }
  const payload = await response.json();
  messageList.replaceChildren(...payload.messages.map(renderMessage));
  messageList.scrollTop = messageList.scrollHeight;
}

askForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (!question) {
    return;
  }
  questionInput.value = "";
  await showMessagesFrom(
    await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    }),
  );
});

newChatButton.addEventListener("click", async () => {
  await showMessagesFrom(await fetch("/api/new-chat", { method: "POST" }));
});

showMessagesFrom(await fetch("/api/messages"));
