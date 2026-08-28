(function (global) {
  async function jsonPost(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Codex launch request failed");
    return data;
  }

  async function poll(token) {
    for (;;) {
      const response = await fetch(`/api/codex/launch/${token}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Launch status unavailable");
      if (data.status === "pending") {
        await new Promise((resolve) => setTimeout(resolve, 500));
        continue;
      }
      return data;
    }
  }

  const PROVIDER_LABELS = { codex: "Codex", claude: "Claude" };

  async function launch(task, button, provider) {
    const chosenProvider = provider || "codex";
    const label = PROVIDER_LABELS[chosenProvider] || chosenProvider;
    const original = button.textContent;
    try {
      button.disabled = true;
      button.textContent = "Previewing...";
      const preview = await jsonPost("/api/codex/prompt-preview", { story_id: task.task_id, provider: chosenProvider });
      if (!global.confirm(`Launch ${label} with this prompt?\n\n${preview.prompt}`)) return;
      button.textContent = "Launching...";
      const started = await jsonPost("/api/codex/launch", { story_id: task.task_id, provider: chosenProvider });
      button.textContent = "Registering...";
      const result = await poll(started.token);
      if (result.status === "registered") {
        button.textContent = "Registered";
        global.location.reload();
        return;
      }
      throw new Error(result.error || "Registration failed");
    } catch (error) {
      button.textContent = `Launch failed: ${error.message}`;
      button.title = error.message;
      return;
    } finally {
      button.disabled = false;
      if (button.textContent === "Previewing...") button.textContent = original;
    }
  }

  global.CodexLaunch = { launch, poll };
})(window);
