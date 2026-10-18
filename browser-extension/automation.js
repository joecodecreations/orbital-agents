/* Placeholder page for an agent session's window: shows the session name. */
const name = new URLSearchParams(location.search).get("session") || "main";
document.getElementById("name").textContent = name;
document.title = "Agent window: " + name;
