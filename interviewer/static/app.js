// Small conveniences only: the console works without this file.
document.querySelectorAll("button[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Select and copy";
    }
    setTimeout(() => { button.textContent = "Copy"; }, 1500);
  });
});

document.querySelectorAll('input[name="project"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    document.getElementById("summary-name").textContent = radio.dataset.name;
    document.getElementById("summary-stack").textContent = radio.dataset.stack;
  });
});
