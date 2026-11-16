const phases = [
  "Find an opportunity", "Validate your offer", "Find and keep customers",
  "Manage money", "Set up operations", "Lead and grow",
];

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function button(label, cssClass, handle) {
  const control = el("button", label, cssClass);
  control.type = "button";
  control.addEventListener("click", handle);
  return control;
}

function external(label, url, cssClass = "link") {
  const link = el("a", label, cssClass);
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

function timeLabel(seconds) {
  const value = Math.floor(seconds);
  return Math.floor(value / 60) + ":" + String(value % 60).padStart(2, "0");
}

export function initializeLearn() {
  const byId = (id) => document.getElementById(id);
  const navigator = byId("learn-topics");
  const topicSelect = byId("learn-topic-select");
  const main = byId("learn-topic");
  const courseStatus = byId("learn-course-status");
  const quizPanel = byId("learn-quiz");
  const quizStatus = byId("learn-quiz-status");
  const historyPanel = byId("learn-history-content");
  const access = byId("learn-access");
  let course;
  let active;
  let loading = false;
  let searching = false;
  let generating = false;
  let historyLoaded = false;
  let topics = new Map();

  async function request(path, { method = "GET", body, withToken = false } = {}) {
    const headers = {};
    const token = withToken ? byId("learn-token").value.trim() : "";
    if (token) headers.Authorization = "Bearer " + token;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch("/learn" + path, {
      method, headers, body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Learn could not complete that request. Try again.");
    }
    return data;
  }

  function setStatus(node, message = "", isError = false) {
    node.textContent = message;
    node.hidden = !message;
    node.classList.toggle("learn-error", isError);
  }

  function showProgress() {
    const mastered = Object.values(course.best_scores).filter((score) => score >= 8).length;
    byId("learn-progress").textContent = mastered + " of " + (course.topics.length * 3) + " lessons mastered";
    byId("learn-track").value = mastered;
    byId("learn-attempt-count").textContent = course.attempt_count + (course.attempt_count === 1 ? " quiz completed" : " quizzes completed");
  }

  function chooseTopic(topic) {
    active = topic;
    topicSelect.value = topic.topic_id;
    setStatus(courseStatus);
    drawNavigator();
    drawTopic();
  }

  function drawNavigator() {
    const openPhases = new Set([...navigator.querySelectorAll("details[open]")].map((details) => Number(details.dataset.phase)));
    const focusedTopic = document.activeElement?.dataset.learnTopic;
    navigator.replaceChildren();
    for (const [index, name] of phases.entries()) {
      const phase = index + 1;
      const group = el("details", null, "learn-phase");
      group.dataset.phase = phase;
      group.open = openPhases.has(phase) || active.phase === phase;
      group.append(el("summary", name));
      const list = el("ul");
      for (const topic of course.topics.filter((row) => row.phase === phase)) {
        const choice = button(topic.title, "learn-topic-link", () => chooseTopic(topic));
        choice.dataset.learnTopic = topic.topic_id;
        if (active.topic_id === topic.topic_id) choice.setAttribute("aria-current", "true");
        const item = el("li");
        item.append(choice);
        list.append(item);
      }
      group.append(list);
      navigator.append(group);
    }
    if (focusedTopic) navigator.querySelector('[data-learn-topic="' + focusedTopic + '"]')?.focus();
  }

  async function findVideos(control) {
    const topic = active;
    searching = true;
    control.disabled = true;
    main.setAttribute("aria-busy", "true");
    setStatus(courseStatus, "Searching YouTube and checking candidate transcripts…");
    try {
      const data = await request("/topics/" + topic.topic_id + "/discover", { method: "POST", withToken: true });
      Object.assign(course.videos, data.videos);
      if (active.topic_id === topic.topic_id) {
        const count = Object.keys(data.videos).length;
        const message = data.warnings.join(" ") || (count + " videos selected.");
        setStatus(courseStatus, message);
      }
    } catch (error) {
      if (active.topic_id === topic.topic_id) setStatus(courseStatus, error.message, true);
    } finally {
      searching = false;
      main.removeAttribute("aria-busy");
      drawTopic();
    }
  }

  function replaceForm(slot, video) {
    const details = el("details", null, "learn-picker");
    details.append(el("summary", video ? "Change video" : "Add a YouTube video"));
    for (const candidate of video?.alternatives || []) {
      const alternative = el("div", null, "learn-alternative");
      alternative.append(external(candidate.title, candidate.url), button("Use video", "chip", () => {
        input.value = candidate.video_id;
        form.requestSubmit();
      }));
      details.append(alternative);
    }
    const form = el("form", null, "learn-picker-form");
    const input = el("input");
    input.id = "learn-picker-" + slot.slot_id;
    input.type = "text";
    input.placeholder = "YouTube URL or video ID";
    input.required = true;
    input.maxLength = 500;
    const label = el("label", "YouTube video");
    label.htmlFor = input.id;
    const save = el("button", "Use video", "chip");
    save.type = "submit";
    const note = el("p", null, "notice");
    note.setAttribute("role", "status");
    note.hidden = true;
    form.append(label, input, save, note);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      save.disabled = true;
      setStatus(note, "Checking English captions…");
      try {
        course.videos[slot.slot_id] = await request("/slots/" + slot.slot_id + "/video", {
          method: "PUT", body: { video: input.value.trim() },
        });
        drawTopic();
        setStatus(courseStatus, "Video updated.");
      } catch (error) {
        setStatus(note, error.message, true);
      } finally {
        save.disabled = false;
      }
    });
    details.append(form);
    return details;
  }

  function drawVideoCard(slot, index) {
    const video = course.videos[slot.slot_id];
    const card = el("article", null, "learn-video");
    const meta = el("div", null, "learn-video-meta");
    meta.append(el("span", "Lesson " + (index + 1)));
    const best = course.best_scores[slot.slot_id];
    if (best !== undefined) meta.append(el("span", "Best " + best + "/" + course.question_count, "chip"));
    card.append(meta, el("h3", slot.brief_title), el("p", slot.coverage_to_check, "learn-description"));
    if (video) {
      const player = el("div", null, "learn-player");
      const play = button("Watch here", "learn-watch", () => {
        const frame = el("iframe");
        frame.title = video.title;
        frame.src = "https://www.youtube-nocookie.com/embed/" + encodeURIComponent(video.video_id) + "?autoplay=1";
        frame.allow = "autoplay; encrypted-media; picture-in-picture; web-share";
        frame.allowFullscreen = true;
        player.replaceChildren(frame);
      });
      const thumb = el("img");
      thumb.src = "https://i.ytimg.com/vi/" + encodeURIComponent(video.video_id) + "/mqdefault.jpg";
      thumb.alt = "";
      thumb.loading = "lazy";
      const playLabel = el("span", "▶  Watch here");
      play.replaceChildren(thumb, playLabel);
      player.append(play);
      const credit = el("div", null, "learn-video-credit");
      credit.append(external(video.title, video.url), el("small", [video.channel,
        video.duration ? timeLabel(video.duration) : null,
        video.caption_source === "automatic" ? "English auto-captions" : "English captions",
      ].filter(Boolean).join(" · ")));
      if (video.comments?.corrections) {
        credit.append(el("small", "Some comments report possible errors. Review the claims before using the video."));
      }
      const take = button("Generate quiz", "primary learn-quiz-button", () => startQuiz(slot));
      take.disabled = generating;
      card.append(player, credit);
      if (video.transcript_fit !== undefined) {
        const selection = el("details", null, "learn-picker learn-selection");
        selection.append(el("summary", "Why this video"));
        const fit = ["Transcript match " + video.transcript_fit + "/100"];
        if (video.comments) fit.push(video.comments.helpful + " of " + video.comments.count + " sampled comments described specific learning");
        if (video.likes != null && video.views) fit.push(video.likes.toLocaleString() + " likes from " + video.views.toLocaleString() + " views");
        if (video.subscribers != null) fit.push(video.subscribers.toLocaleString() + " channel subscribers");
        selection.append(el("p", fit.join(" · "), "learn-description"));
        card.append(selection);
      }
      card.append(take);
    } else {
      card.append(el("p", "No video selected yet.", "learn-placeholder"));
    }
    card.append(replaceForm(slot, video));
    return card;
  }

  function drawTopic() {
    main.hidden = false;
    const heading = el("header", null, "learn-topic-head");
    const info = el("div");
    info.append(el("p", "Topic " + Number(active.topic_id.slice(1)) + " of " + course.topics.length, "learn-kicker"),
      el("h2", active.title), el("p", active.learning_outcome, "learn-description"));
    const hasVideos = active.video_slots.some((slot) => course.videos[slot.slot_id]);
    const discover = button(hasVideos ? "Find more videos" : "Find videos", "primary", () => findVideos(discover));
    discover.disabled = searching;
    heading.append(info, discover);
    const videos = el("div", null, "learn-video-grid");
    active.video_slots.forEach((slot, i) => videos.append(drawVideoCard(slot, i)));
    const exercise = el("aside", null, "learn-exercise");
    exercise.append(el("h3", "Put it into practice"), el("p", active.learner_assignment),
      el("p", active.assignment_check, "learn-description"));
    main.replaceChildren(heading, videos, exercise);
  }

  async function startQuiz(slot) {
    generating = true;
    for (const control of main.querySelectorAll(".learn-quiz-button")) control.disabled = true;
    setStatus(quizStatus, "Generating ten questions from the transcript. This may take a few minutes…");
    quizStatus.scrollIntoView({ block: "center" });
    try {
      const attempt = await request("/slots/" + slot.slot_id + "/quiz", { method: "POST", withToken: true });
      setStatus(quizStatus);
      drawQuiz(attempt);
    } catch (error) {
      if (error.message.includes("Hugging Face token")) access.open = true;
      setStatus(quizStatus, error.message, true);
    } finally {
      generating = false;
      for (const control of main.querySelectorAll(".learn-quiz-button")) control.disabled = false;
      for (const control of quizPanel.querySelectorAll("[data-learn-retry]")) control.disabled = false;
    }
  }

  function drawQuiz(attempt) {
    quizPanel.hidden = false;
    const graded = Boolean(attempt.submitted_at);
    const heading = el("h2", graded ? "Quiz result" : "Video quiz");
    heading.tabIndex = -1;
    const title = el("p", attempt.video_title, "learn-description");
    const form = el("form", null, "learn-questions");
    const results = el("div", null, "learn-result");
    if (graded) {
      results.append(el("strong", attempt.score + " / " + attempt.questions.length),
        el("span", attempt.score >= 8 ? "Mastered" : "Keep practicing"));
      const slot = topics.get(attempt.slot_id);
      if (slot && course.videos[slot.slot_id]?.video_id === attempt.video_id) {
        const retry = button("Generate a new quiz", "primary", () => {
          retry.disabled = true;
          startQuiz(slot);
        });
        retry.dataset.learnRetry = "";
        retry.disabled = generating;
        results.append(retry);
      }
    }
    const evidenceLink = (cue) => external("Review at " + timeLabel(cue.start),
      "https://www.youtube.com/watch?v=" + encodeURIComponent(attempt.video_id) + "&t=" + Math.floor(cue.start) + "s");
    function choicesMenu(name, options, selected) {
      const menu = el("select");
      menu.name = name;
      const placeholder = el("option", "Choose a phrase");
      placeholder.value = "";
      menu.append(placeholder);
      options.forEach((text, i) => {
        const option = el("option", text);
        option.value = String(i);
        menu.append(option);
      });
      menu.required = !graded;
      menu.disabled = graded;
      menu.value = selected === undefined ? "" : String(selected);
      return menu;
    }
    attempt.questions.forEach((question, i) => {
      const group = el("fieldset", null, "learn-question");
      group.append(el("legend", (i + 1) + ". " + question.question));
      if (question.type === "match") {
        const matches = el("div", null, "learn-match");
        question.pairs.forEach((pair, j) => {
          const line = el("div", null, "learn-match-row");
          const clue = el("label", pair.prompt);
          const selected = graded ? attempt.answers[i][j] : undefined;
          const menu = choicesMenu("answer-" + i + "-" + j, question.choices, selected);
          menu.id = "learn-match-" + i + "-" + j;
          clue.htmlFor = menu.id;
          clue.append(menu);
          line.append(clue);
          if (graded) {
            const correct = question.answer_indices[j];
            line.classList.add(selected === correct ? "learn-correct" : "learn-incorrect");
            line.append(el("small", "Correct match: " + question.choices[correct]),
              evidenceLink(pair));
          }
          matches.append(line);
        });
        group.append(matches);
        if (!graded) group.append(el("small", "Use each phrase once.", "learn-description"));
      } else if (question.type === "fill_blank") {
        const menu = choicesMenu("answer-" + i, question.choices, graded ? attempt.answers[i] : undefined);
        menu.setAttribute("aria-label", "Choose the phrase for question " + (i + 1));
        group.append(menu);
        if (graded) {
          const correct = attempt.answers[i] === question.answer_index;
          group.append(el("p", "Correct answer: " + question.choices[question.answer_index],
            "learn-choice " + (correct ? "learn-correct" : "learn-incorrect")),
            el("p", question.explanation, "learn-description"), evidenceLink(question));
        }
      } else {
        question.choices.forEach((choice, j) => {
          const label = el("label", null, "learn-choice");
          const input = el("input");
          input.type = "radio";
          input.name = "answer-" + i;
          input.value = String(j);
          input.required = !graded;
          input.disabled = graded;
          input.checked = graded && attempt.answers[i] === j;
          if (graded && j === question.answer_index) label.classList.add("learn-correct");
          else if (graded && input.checked) label.classList.add("learn-incorrect");
          const message = graded && j === question.answer_index ? choice + " (Correct)" : choice;
          label.append(input, el("span", message));
          group.append(label);
        });
        if (graded) group.append(el("p", question.explanation, "learn-description"), evidenceLink(question));
      }
      form.append(group);
    });
    if (!graded) {
      const submit = el("button", "Submit answers", "primary");
      submit.type = "submit";
      const status = el("p", null, "notice");
      status.setAttribute("role", "status");
      status.hidden = true;
      form.append(submit, status);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const values = new FormData(form);
        const answers = attempt.questions.map((question, i) => question.type === "match"
          ? question.pairs.map((_, j) => Number(values.get("answer-" + i + "-" + j)))
          : Number(values.get("answer-" + i)));
        if (answers.some((answer) => Array.isArray(answer) && new Set(answer).size !== answer.length)) {
          setStatus(status, "Use each phrase once in the matching question.", true);
          return;
        }
        submit.disabled = true;
        setStatus(status, "Scoring your answers…");
        try {
          const result = await request("/attempts/" + attempt.id + "/submit", { method: "POST", body: { answers } });
          course.best_scores[result.slot_id] = Math.max(course.best_scores[result.slot_id] ?? 0, result.score);
          course.attempt_count += 1;
          historyLoaded = false;
          showProgress();
          drawQuiz(result);
          if (byId("learn-history").open) loadHistory();
        } catch (error) {
          setStatus(status, error.message, true);
          submit.disabled = false;
        }
      });
    }
    quizPanel.replaceChildren(heading, title, results, form);
    heading.focus({ preventScroll: true });
    quizPanel.scrollIntoView({ block: "start" });
  }

  async function loadHistory() {
    if (historyLoaded) return;
    historyPanel.replaceChildren(el("p", "Loading results…", "notice"));
    try {
      const { attempts } = await request("/attempts");
      historyPanel.replaceChildren();
      if (!attempts.length) historyPanel.append(el("p", "Your completed quizzes will appear here.", "notice"));
      for (const row of attempts) {
        const item = el("div", null, "learn-history-row");
        const label = el("div");
        label.append(el("strong", row.video_title), el("small", new Date(row.submitted_at).toLocaleString()));
        const review = button("Review", "chip", async () => {
          try {
            setStatus(quizStatus);
            drawQuiz(await request("/attempts/" + row.id));
          } catch (error) {
            setStatus(quizStatus, error.message, true);
          }
        });
        item.append(label, el("span", row.score + "/" + course.question_count), review);
        historyPanel.append(item);
      }
      historyLoaded = true;
    } catch (error) {
      historyPanel.replaceChildren(el("p", error.message, "notice learn-error"),
        button("Try again", "chip", loadHistory));
    }
  }

  topicSelect.addEventListener("change", () => {
    const topic = course.topics.find((item) => item.topic_id === topicSelect.value);
    if (topic) chooseTopic(topic);
  });
  byId("learn-history").addEventListener("toggle", (event) => {
    if (event.currentTarget.open && course) loadHistory();
  });

  return async function showLearn() {
    if (loading || course) return;
    loading = true;
    setStatus(courseStatus, "Loading the course…");
    try {
      course = await request("/course");
      active = course.topics[0];
      for (const [index, name] of phases.entries()) {
        const group = el("optgroup");
        group.label = name;
        for (const topic of course.topics.filter((item) => item.phase === index + 1)) {
          const option = el("option", topic.title);
          option.value = topic.topic_id;
          group.append(option);
        }
        topicSelect.append(group);
      }
      topicSelect.value = active.topic_id;
      byId("learn-mobile-topics").hidden = false;
      topics = new Map(course.topics.flatMap((topic) => topic.video_slots.map((slot) => [slot.slot_id, slot])));
      byId("learn-track").max = course.topics.length * 3;
      if (course.space_token_configured) byId("learn-token-note").textContent = "The app already has access configured. You can use another token for this session.";
      setStatus(courseStatus);
      showProgress();
      drawNavigator();
      drawTopic();
    } catch (error) {
      setStatus(courseStatus, error.message, true);
      courseStatus.append(" ", button("Try again", "chip", showLearn));
    } finally {
      loading = false;
    }
  };
}
