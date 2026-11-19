() => {
  let active = null;
  let remembered = null;
  let vocabularyBinding = null;
  const bindVocabulary = () => {
    const panel = document.querySelector('#vocabulary-panel') || document.querySelector('#vocabulary-feed');
    const root = document.querySelector('#study-player');
    const media = root?.querySelector('video');
    const currentNode = root?.querySelector('.vocab-current');
    const firstItem = panel?.querySelector('.vocab-item');
    if (vocabularyBinding?.currentNode === currentNode && vocabularyBinding?.firstItem === firstItem && vocabularyBinding?.panel === panel && vocabularyBinding?.media === media && vocabularyBinding?.source === root?.dataset.segments && vocabularyBinding?.owner === root?.dataset.videoId) {
      vocabularyBinding.update?.();
      return;
    }
    vocabularyBinding?.controller.abort();
    vocabularyBinding = null;
    if (!panel || !media || !currentNode) return;
    const controller = new AbortController();
    vocabularyBinding = {panel, media, controller, currentNode, firstItem, source: root.dataset.segments, owner: root.dataset.videoId};
    const current = currentNode;
    const items = [...panel.querySelectorAll('.vocab-item')];
    let shown = null;
    const update = () => {
      const matchesSource = panel.dataset.owner === root.dataset.videoId && panel.dataset.source === root.dataset.segments;
      panel.hidden = !matchesSource;
      const locked = Boolean(active?.tornadoLock);
      const matching = matchesSource ? items.filter(item => media.currentTime >= Number(item.dataset.vocabStart) && media.currentTime < Number(item.dataset.vocabEnd)) : [];
      for (const item of items) {
        item.classList.toggle('active', matching.includes(item));
        item.disabled = locked;
        item.title = locked ? '请先关闭龙卷风再定位词汇' : '暂停并定位到对应字幕';
      }
      const key = matching.map(item => item.innerHTML).join('\n');
      if (key === shown) return;
      shown = key;
      const selectedItem = matching[0];
      if (selectedItem && panel.getClientRects().length) {
        const list = panel.querySelector('.vocab-list');
        list.scrollTop = selectedItem.offsetTop - list.offsetTop - list.clientHeight / 2 + selectedItem.clientHeight / 2;
      }
      current.hidden = matching.length === 0;
      current.replaceChildren();
      for (const item of matching) {
        const card = document.createElement('article');
        card.className = 'vocab-card';
        for (const child of item.children) card.append(child.cloneNode(true));
        current.append(card);
      }
    };
    for (const item of items) item.addEventListener('click', () => {
      if (active?.tornadoLock || panel.hidden) return;
      // A manual vocabulary selection pauses at the original cue, even if skip is enabled.
      active?.clearWait();
      media.pause();
      media.currentTime = Number(item.dataset.vocabStart);
      update();
    }, {signal: controller.signal});
    for (const event of ['timeupdate', 'seeked', 'loadedmetadata', 'pause', 'play']) {
      media.addEventListener(event, update, {signal: controller.signal});
    }
    root.addEventListener('click', () => queueMicrotask(update), {signal: controller.signal});
    vocabularyBinding.update = update;
    update();
  };
  const bind = () => {
    bindVocabulary();
    const root = document.querySelector('#study-player');
    const videoId = root?.dataset.videoId;
    const segmentData = root?.dataset.segments;
    const media = root?.querySelector('video');
    if (active && (active.root !== root || active.video !== media ||
        active.videoId !== videoId || active.segmentData !== segmentData)) {
      // Snapshot identity before a framework update can reuse these DOM nodes.
      remembered = {
        videoId: active.videoId,
        segmentData: active.segmentData,
        tornadoLock: active.tornadoLock,
        time: active.video.currentTime,
        rate: active.video.playbackRate,
        playing: active.playing,
        repeat: active.repeat.checked,
        count: active.count.value,
        pause: active.pause.value,
        endPause: active.endPause.value,
        skipWords: active.skipWords.value,
        expanded: active.expanded,
        merge: active.merge.checked,
        skipEnabled: active.skipMode.checked,
        mergeMinWords: active.mergeMinWords.value,
        mergeMaxWords: active.mergeMaxWords.value,
        settingsOpen: active.settings.open,
      };
      active.dispose();
      active = null;
    }
    if (!root || active) return;
    const video = root.querySelector('video');
    const rawSegments = JSON.parse(root.dataset.segments || '[]');
    let segments = [], sourceToGroup = [];
    const merge = root.querySelector('.merge-mode');
    const skipMode = root.querySelector('.skip-mode');
    const mergeMinWords = root.querySelector('.merge-min-words');
    const mergeMaxWords = root.querySelector('.merge-max-words');
    const normalizeWords = (value, fallback, max) => {
      const n = Number(value);
      return Number.isFinite(n) && n > 0 ? Math.min(max, Math.max(1, Math.floor(n))) : fallback;
    };
    const normalizeWordLimits = () => {
      mergeMinWords.value = String(normalizeWords(mergeMinWords.value, 8, 100));
      mergeMaxWords.value = String(Math.max(Number(mergeMinWords.value), normalizeWords(mergeMaxWords.value, 20, 200)));
    };
    const countWords = text => (text.match(/[\p{L}\p{N}]+(?:['’\-][\p{L}\p{N}]+)*/gu) || []).length;
    const settings = root.querySelector('.player-settings');
    const status = root.querySelector('.play-status');
    const repeat = root.querySelector('.repeat-mode');
    const count = root.querySelector('.repeat-count');
    const pause = root.querySelector('.pause-factor');
    const endPause = root.querySelector('.end-pause-factor');
    const speed = root.querySelector('.play-speed');
    const skipWords = root.querySelector('.skip-words');
    const normalizeThreshold = (value) => {
      const number = Number(value);
      return Number.isFinite(number) ? Math.max(0, Math.floor(number)) : 0;
    };
    const speakerOf = cue => {
      const value = String(cue.speaker || '').trim();
      return ['unknown', 'unk', 'none'].includes(value.toLowerCase()) ? '' : value;
    };
    const adjacent = (left, right) => left && right && right.start >= left.start &&
      right.start - left.end <= 1.5;
    const sameTurn = (left, right) => adjacent(left, right) &&
      speakerOf(left) !== '' && speakerOf(left) === speakerOf(right);
    const rebuildGroups = () => {
      segments = [];
      sourceToGroup = [];
      for (const [i, cue] of rawSegments.entries()) {
        const words = countWords(cue.text);
        const speaker = speakerOf(cue);
        // Route using original neighbors, even when word limits split a turn.
        const continuous = sameTurn(rawSegments[i - 1], cue) || sameTurn(cue, rawSegments[i + 1]);
        const mergeRoute = merge.checked && (continuous || !speaker);
        const skipped = skipMode.checked && !(merge.checked && continuous) &&
          words < normalizeThreshold(skipWords.value);
        const previous = segments.at(-1);
        // Never bridge a skipped cue: its audio would otherwise re-enter the group.
        if (mergeRoute && !skipped && previous?.mergeRoute && !previous.skipped &&
            previous.speaker === speaker && previous.last === i - 1 &&
            adjacent(rawSegments[i - 1], cue) &&
            previous.words < Number(mergeMinWords.value) &&
            previous.words + words <= Number(mergeMaxWords.value)) {
          previous.end = Math.max(previous.end, cue.end);
          previous.text += ' ' + cue.text;
          previous.last = i;
          previous.words += words;
        } else {
          segments.push({...cue, speaker, words, skipped, mergeRoute, first: i, last: i});
        }
        sourceToGroup[i] = segments.length - 1;
      }
    };
    const shouldSkip = i => Boolean(segments[i]?.skipped);
    const playableIndex = (start) => {
      while (start < segments.length && shouldSkip(start)) start += 1;
      return start;
    };
    const nextSentence = root.querySelector('.next-sentence');
    const playbackToggle = root.querySelector('.playback-toggle');
    const tornado = root.querySelector('.tornado-mode');
    let tornadoLock = null;
    const sentenceButtons = [...root.querySelectorAll('[data-seek]')];
    const sentenceList = root.querySelector('.sentence-list');
    nextSentence.disabled = !rawSegments.length;
    const expand = root.querySelector('.sentence-expand');
    const preferenceKey = 'video-study-player-v1';
    let preferences = {};
    try { preferences = JSON.parse(localStorage.getItem(preferenceKey) || '{}') || {}; } catch { /* Browser storage is optional. */ }
    let index = -1, played = 0, timer = null, internalSeek = false, transitioning = false;
    let viewIndex = -1, expanded = false;

    const controller = new AbortController();
    const on = (target, event, callback, options = {}) =>
      target.addEventListener(event, callback, {...options, signal: controller.signal});
    const syncPlaybackToggle = () => {
      const running = !video.paused || timer !== null || internalSeek;
      playbackToggle.textContent = running ? '暂停' : '启动';
      playbackToggle.setAttribute('aria-label', playbackToggle.textContent);
      playbackToggle.title = running ? '暂停播放并取消等待' : '读取当前设置并启动播放';
      if (active?.video === video) active.playing = running;
    };
    const clearWait = () => {
      if (timer !== null) clearTimeout(timer);
      timer = null;
      syncPlaybackToggle();
    };
    const playVideo = () => video.play().catch(() => {
      if (active?.video !== video) return;
      status.textContent = '播放失败，请检查视频是否可用后重试';
      syncPlaybackToggle();
    });
    active = {root, video, videoId, segmentData, repeat, count, pause, endPause, skipWords, skipMode, merge, mergeMinWords, mergeMaxWords, settings, clearWait,
      playing: false, expanded: false, dispose: () => {
        clearWait();
        controller.abort();
        internalSeek = false;
        transitioning = false;
        video.pause();
      }};
    video.pause();
    count.value = preferences.count || count.value;
    pause.value = preferences.pause ?? pause.value;
    endPause.value = preferences.endPause ?? endPause.value;
    speed.value = preferences.speed || speed.value;
    skipWords.value = String(normalizeThreshold(preferences.skipWords));
    merge.checked = preferences.merge !== false;
    skipMode.checked = preferences.skipEnabled !== false;
    mergeMinWords.value = preferences.mergeMinWords ?? 8;
    mergeMaxWords.value = preferences.mergeMaxWords ?? 20;
    normalizeWordLimits();
    settings.open = preferences.settingsOpen !== false;
    rebuildGroups();
    repeat.checked = Boolean(preferences.repeat && segments.length);
    const savePreferences = () => {
      try {
        localStorage.setItem(preferenceKey, JSON.stringify({repeat: repeat.checked, count: count.value,
          pause: pause.value, endPause: endPause.value, speed: speed.value, skipWords: normalizeThreshold(skipWords.value),
          merge: merge.checked, skipEnabled: skipMode.checked,
          mergeMinWords: Number(mergeMinWords.value), mergeMaxWords: Number(mergeMaxWords.value), settingsOpen: settings.open}));
      } catch { /* Playback remains usable when browser storage is unavailable. */ }
    };
    on(settings, 'toggle', savePreferences);
    on(video, 'play', () => {
      if (active?.video === video) active.playing = true;
      skipCurrent();
      syncPlaybackToggle();
    });
    on(video, 'pause', () => {
      syncPlaybackToggle();
    });
    on(video, 'ended', syncPlaybackToggle);
    on(video, 'error', () => {
      clearWait(); internalSeek = false; transitioning = false;
      video.pause();
      status.textContent = '视频加载失败，请检查文件后重试';
      syncPlaybackToggle();
    });
    const seek = (time) => {
      if (Math.abs(video.currentTime - time) < 0.001) {
        playVideo();
        return;
      }
      internalSeek = true;
      transitioning = true;
      video.pause();
      video.currentTime = time;
      syncPlaybackToggle();
    };
    const locate = (time) => segments.findIndex((s) => time < s.end - 0.04);
    const updateWindow = (force = false) => {
      if (!sentenceButtons.length) return;
      const located = locate(video.currentTime);
      const current = repeat.checked && index >= 0 ? index : located < 0 ? segments.length - 1 : located;
      const group = tornadoLock || segments[current];
      if (!group) return;
      nextSentence.disabled = Boolean(tornadoLock) || playableIndex(current + 1) >= segments.length;
      if (!force && current === viewIndex) return;
      viewIndex = current;
      sentenceButtons.forEach((button, i) => {
        button.hidden = !expanded && (i < group.first - 3 || i > group.last + 3);
        button.classList.toggle('active', i >= group.first && i <= group.last);
        if (i === group.first) button.setAttribute('aria-current', 'true');
        else button.removeAttribute('aria-current');
      });
      const currentButton = sentenceButtons[group.first];
      sentenceList.scrollTop = currentButton.offsetTop - sentenceList.offsetTop - sentenceList.clientHeight / 2 + currentButton.clientHeight / 2;
    };
    on(expand, 'click', () => {
      expanded = !expanded;
      active.expanded = expanded;
      expand.textContent = expanded ? '收起' : '展开全部';
      expand.setAttribute('aria-expanded', String(expanded));
      updateWindow(true);
    });
    on(nextSentence, 'click', () => {
      if (!segments.length || tornadoLock) return;
      const current = repeat.checked && index >= 0 ? index : locate(video.currentTime);
      if (current < 0 || current >= segments.length - 1) return;
      clearWait();
      jumpToSentence(current + 1);
    });
    const syncTornado = () => {
      tornado.disabled = !rawSegments.length;
      tornado.setAttribute('aria-pressed', String(Boolean(tornadoLock)));
      tornado.classList.toggle('is-active', Boolean(tornadoLock));
      sentenceButtons.forEach(button => { button.disabled = Boolean(tornadoLock); });
      active.tornadoLock = tornadoLock;
      updateWindow(true);
    };
    const show = () => {
      if (tornadoLock) { status.textContent = `龙卷风 · 无限循环 · ${tornadoLock.text}`; return; }
      status.textContent = repeat.checked && index >= 0 && index < segments.length
        ? `${index + 1}/${segments.length}${merge.checked ? ` 组（原字幕 ${rawSegments.length} 句）` : " 句"} · 第 ${played + 1}/${count.value} 遍 · ${segments[index].text}`: '';
    };
    const jumpToSentence = (start) => {
      clearWait();
      index = playableIndex(start);
      played = 0;
      if (index >= segments.length) {
        // No eligible subtitle remains: stop rather than seek/play in a loop.
        internalSeek = false;
        transitioning = false;
        video.pause();
        repeat.checked = false;
        const end = segments.at(-1)?.end ?? video.currentTime;
        video.currentTime = Number.isFinite(video.duration) ? Math.min(end, video.duration) : end;
        index = -1;
        updateWindow(true);
        status.textContent = start < segments.length ? '后续字幕均已跳过' : '';
        return;
      }
      seek(segments[index].start);
      show();
      updateWindow(true);
    };
    const skipCurrent = () => {
      if (tornadoLock || transitioning || !segments.length || (video.paused && timer === null)) return false;
      // locate() also points at the next cue during a gap; only skip once it starts.
      const current = repeat.checked && index >= 0 ? index : locate(video.currentTime);
      if (current < 0 || current >= segments.length || video.currentTime < segments[current].start || !shouldSkip(current)) return false;
      jumpToSentence(current);
      return true;
    };
    const next = () => jumpToSentence(index + 1);
    on(video, 'seeked', () => {
      if (internalSeek) {
        internalSeek = false;
        transitioning = false;
        playVideo();
        return;
      }
      clearWait();
      if (tornadoLock) {
        if (video.currentTime < tornadoLock.start || video.currentTime >= tornadoLock.end) {
          if (!video.paused) seek(tornadoLock.start);
          else video.currentTime = tornadoLock.start;
        }
        updateWindow(true); show(); return;
      }
      if (repeat.checked) { index = locate(video.currentTime); played = 0; show(); }
      if (skipCurrent()) return;
      updateWindow();
    });
    on(video, 'timeupdate', () => {
      if (skipCurrent()) return;
      updateWindow();
      if ((!repeat.checked && !tornadoLock) || transitioning || timer !== null || !segments.length || (video.paused && !video.ended)) return;
      if (index < 0 && !tornadoLock) { index = locate(video.currentTime); played = 0; show(); }
      if (!tornadoLock && (index < 0 || index >= segments.length)) { repeat.checked = false; show(); return; }
      const segment = tornadoLock || segments[index];
      if (video.currentTime < segment.end - 0.04) return;
      video.pause();
      const factor = tornadoLock || played + 1 < Number(count.value) ? Number(pause.value) : Number(endPause.value);
      const delay = (segment.end - segment.start) * factor / video.playbackRate * 1000;
      timer = setTimeout(() => {
        timer = null;
        if (tornadoLock) { seek(tornadoLock.start); show(); return; }
        if (!repeat.checked) return;
        if (played + 1 < Number(count.value)) {
          played += 1;
          seek(segment.start);
          show();
        } else next();
      }, delay);
      syncPlaybackToggle();
    });
    on(repeat, 'change', () => {
      if (tornadoLock) { savePreferences(); show(); return; }
      clearWait(); index = repeat.checked ? locate(video.currentTime) : -1; played = 0;
      if (repeat.checked && !segments.length) { repeat.checked = false; status.textContent = '请先生成字幕'; return; }
      if (repeat.checked && index < 0) { jumpToSentence(0); }
      savePreferences();
      updateWindow(true);
      show();
    });
    const updateMergeLabel = () => {
      skipWords.disabled = !skipMode.checked;
      mergeMinWords.disabled = !merge.checked;
      mergeMaxWords.disabled = !merge.checked;
      nextSentence.textContent = merge.checked ? '下一组' : '下一句';
      nextSentence.title = merge.checked ? '跳转到下一组字幕' : '跳转到下一句';
    };
    const updateGrouping = () => {
      normalizeWordLimits();
      const shouldResume = !video.paused || timer !== null || internalSeek;
      const oldCurrent = repeat.checked && index >= 0 ? index : locate(video.currentTime);
      const sourceIndex = segments[oldCurrent]?.first;
      clearWait();
      internalSeek = false;
      transitioning = false;
      rebuildGroups();
      index = repeat.checked ? locate(video.currentTime) : -1;
      played = 0;
      viewIndex = -1;
      updateMergeLabel();
      savePreferences();
      updateWindow(true);
      show();
      if (shouldResume) {
        if (tornadoLock) {
          if (video.currentTime < tornadoLock.start || video.currentTime >= tornadoLock.end - 0.04) seek(tornadoLock.start);
          else playVideo();
          return;
        }
        const located = locate(video.currentTime);
        const resumeIndex = located >= 0 ? located : sourceToGroup[sourceIndex];
        if (shouldSkip(resumeIndex)) jumpToSentence(resumeIndex);
        else if (!skipCurrent()) playVideo();
      }
    };
    on(playbackToggle, 'click', () => {
      const running = !video.paused || timer !== null || internalSeek;
      clearWait();
      internalSeek = false;
      transitioning = false;
      video.pause();
      if (running) { syncPlaybackToggle(); return; }
      // Read live controls, including edits that have not fired change/blur.
      const number = (control, fallback, min, max, integer = false) => {
        const parsed = control.value.trim() === '' ? fallback : Number(control.value);
        const value = Number.isFinite(parsed) ? Math.max(min, Math.min(max, parsed)) : fallback;
        control.value = String(integer ? Math.floor(value) : value);
      };
      number(count, 3, 1, 20, true);
      number(pause, 0, 0, 10);
      number(endPause, 0, 0, 10);
      skipWords.value = String(normalizeThreshold(skipWords.value));
      if (![0.5, 0.75, 1, 1.25, 1.5, 2].includes(Number(speed.value))) speed.value = '1';
      video.playbackRate = Number(speed.value);
      if (!rawSegments.length) repeat.checked = false;
      updateGrouping();
      if (video.ended || (Number.isFinite(video.duration) && video.currentTime >= video.duration)) video.currentTime = 0;
      const current = locate(video.currentTime);
      if (tornadoLock) {
        if (video.currentTime < tornadoLock.start || video.currentTime >= tornadoLock.end - 0.04) seek(tornadoLock.start);
        else playVideo();
        syncPlaybackToggle(); return;
      }
      if (segments.length && (shouldSkip(current) || (repeat.checked && current < 0))) {
        jumpToSentence(current < 0 ? 0 : current);
      } else if (repeat.checked && current >= 0 && video.currentTime < segments[current].start) {
        jumpToSentence(current);
      } else {
        index = repeat.checked ? current : -1;
        show();
        playVideo();
      }
      syncPlaybackToggle();
    });
    on(tornado, 'click', () => {
      if (!segments.length) return;
      const resume = !video.paused || timer !== null || internalSeek;
      // The displayed unit remains authoritative during repeat waits/at cue end.
      const current = repeat.checked && index >= 0 ? index : viewIndex >= 0 ? viewIndex : locate(video.currentTime);
      clearWait(); internalSeek = false; transitioning = false;
      if (tornadoLock) {
        tornadoLock = null;
        index = repeat.checked ? locate(video.currentTime) : -1;
        played = 0;
        syncTornado(); show();
        if (resume) { if (!skipCurrent()) playVideo(); }
      } else {
        const group = segments[current];
        if (!group) return;
        tornadoLock = {...group};
        syncTornado(); show();
        seek(tornadoLock.start);
      }
      syncPlaybackToggle();
    });
    on(merge, 'change', updateGrouping);
    on(skipMode, 'change', updateGrouping);
    for (const [control, max] of [[mergeMinWords, 100], [mergeMaxWords, 200]]) {
      on(control, 'input', () => {
        const n = Number(control.value);
        if (Number.isInteger(n) && n >= 1 && n <= max &&
            Number(mergeMaxWords.value) >= Number(mergeMinWords.value)) updateGrouping();
      });
      on(control, 'change', updateGrouping);
    }
    for (const control of [count, pause, endPause]) on(control, 'change', savePreferences);
    const updateSkipWords = (event) => {
      if (event.type === 'change') skipWords.value = String(normalizeThreshold(skipWords.value));
      updateGrouping();
    };
    on(skipWords, 'input', updateSkipWords);
    on(skipWords, 'change', updateSkipWords);
    on(speed, 'change', () => { video.playbackRate = Number(speed.value); savePreferences(); });
    on(video, 'loadedmetadata', () => { video.playbackRate = Number(speed.value); });
    root.querySelectorAll('[data-seek]').forEach((button) => on(button, 'click', () => {
      if (!tornadoLock) jumpToSentence(sourceToGroup[Number(button.dataset.seek)]);
    }));
    if (!remembered || remembered.videoId !== root.dataset.videoId) {
      const resume = Number(root.dataset.resume);
      if (Number.isFinite(resume) && resume > 0) {
        const restorePosition = () => {
          video.currentTime = resume >= video.duration - 0.5 ? 0 : Math.min(resume, video.duration || resume);
          video.pause();
        };
        if (video.readyState >= 1) restorePosition();
        else on(video, 'loadedmetadata', restorePosition, {once: true});
      }
    }
    if (remembered) {
      const previous = remembered;
      remembered = null;
      if (previous.videoId === root.dataset.videoId) {
        if (previous.segmentData === segmentData) tornadoLock = previous.tornadoLock || null;
        count.value = previous.count;
        pause.value = previous.pause;
        endPause.value = previous.endPause;
        skipWords.value = previous.skipWords;
        merge.checked = previous.merge;
        skipMode.checked = previous.skipEnabled;
        mergeMinWords.value = previous.mergeMinWords;
        mergeMaxWords.value = previous.mergeMaxWords;
        normalizeWordLimits();
        settings.open = previous.settingsOpen;
        rebuildGroups();
        speed.value = String(previous.rate);
        repeat.checked = previous.repeat && segments.length > 0;
        expanded = previous.expanded;
        active.expanded = expanded;
        expand.textContent = expanded ? '收起' : '展开全部';
        expand.setAttribute('aria-expanded', String(expanded));
        index = repeat.checked ? locate(previous.time) : -1;
        const restore = () => {
          video.playbackRate = previous.rate;
          video.currentTime = Math.min(previous.time, video.duration || previous.time);
          if (previous.playing) playVideo();
        };
        if (video.readyState >= 1) restore();
        else on(video, 'loadedmetadata', restore, {once: true});
      }
    }
    updateMergeLabel();
    syncTornado();
    if (repeat.checked && index < 0 && segments.length) index = locate(video.currentTime);
    updateWindow(true);
    show();
    syncPlaybackToggle();
  };
  new MutationObserver(bind).observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['data-video-id', 'data-segments', 'src']});
  bind();
}
