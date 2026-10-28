const button = document.getElementById("copy-prompt");
const text = document.getElementById("prompt-text");
if (button && text) {
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text.textContent || "");
      button.textContent = "Copied";
    } catch (_error) {
      button.textContent = "Select the prompt to copy it";
    }
  });
}
