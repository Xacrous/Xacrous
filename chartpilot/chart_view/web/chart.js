// ChartPilot candlestick chart, driven entirely by the Python side over a
// QWebChannel bridge. Uses TradingView's Lightweight Charts (Apache-2.0);
// attribution notice lives on the About screen per the license condition.
(function () {
  "use strict";

  const container = document.getElementById("chart");
  const chart = LightweightCharts.createChart(container, {
    layout: { background: { color: "#131722" }, textColor: "#d1d4dc" },
    grid: {
      vertLines: { color: "#1e222d" },
      horzLines: { color: "#1e222d" },
    },
    timeScale: { timeVisible: true, secondsVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });

  const candleSeries = chart.addCandlestickSeries({
    upColor: "#16C784",
    downColor: "#EA3943",
    borderVisible: false,
    wickUpColor: "#16C784",
    wickDownColor: "#EA3943",
  });

  const volumeSeries = chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "volume",
  });
  chart.priceScale("volume").applyOptions({
    scaleMargins: { top: 0.85, bottom: 0 },
  });

  // Overlay set differs by mode (swing: SMA20/50/200; scalp: EMA9/21 +
  // Bollinger Bands), so series are created/torn down dynamically to match
  // whatever keys the current payload carries.
  const overlayColorPalette = {
    sma20: "#f5a623", sma50: "#3a86ff", sma200: "#a259ff",
    ema9: "#f5a623", ema21: "#3a86ff",
    bb_lower: "#5c6370", bb_mid: "#8a8f9c", bb_upper: "#5c6370",
  };
  const overlaySeries = {};

  function syncOverlays(overlays) {
    const keys = Object.keys(overlays);
    for (const existingKey of Object.keys(overlaySeries)) {
      if (!keys.includes(existingKey)) {
        chart.removeSeries(overlaySeries[existingKey]);
        delete overlaySeries[existingKey];
      }
    }
    for (const key of keys) {
      if (!overlaySeries[key]) {
        overlaySeries[key] = chart.addLineSeries({
          color: overlayColorPalette[key] || "#888888",
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: false,
        });
      }
      overlaySeries[key].setData(overlays[key]);
    }
  }

  let signalPriceLines = [];

  function clearSignalLines() {
    for (const line of signalPriceLines) {
      candleSeries.removePriceLine(line);
    }
    signalPriceLines = [];
  }

  function drawSignalLines(signal) {
    clearSignalLines();
    if (!signal) return;
    signalPriceLines.push(
      candleSeries.createPriceLine({
        price: signal.entry,
        color: "#d1d4dc",
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Solid,
        axisLabelVisible: true,
        title: "Entry",
      }),
      candleSeries.createPriceLine({
        price: signal.take_profit,
        color: "#16C784",
        lineWidth: 2,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: "TP",
      }),
      candleSeries.createPriceLine({
        price: signal.stop_loss,
        color: "#EA3943",
        lineWidth: 2,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: "SL",
      })
    );
  }

  let pivotPriceLines = [];

  function clearPivotLines() {
    for (const line of pivotPriceLines) candleSeries.removePriceLine(line);
    pivotPriceLines = [];
  }

  function drawPivotLines(pivots) {
    clearPivotLines();
    if (!pivots) return;
    const resistanceLevels = [["r1", pivots.r1], ["r2", pivots.r2], ["r3", pivots.r3]];
    const supportLevels = [["s1", pivots.s1], ["s2", pivots.s2], ["s3", pivots.s3]];
    pivotPriceLines.push(
      candleSeries.createPriceLine({
        price: pivots.pp, color: "#8a8f9c", lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true, title: "PP",
      })
    );
    for (const [label, price] of resistanceLevels) {
      pivotPriceLines.push(candleSeries.createPriceLine({
        price, color: "#EA3943", lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true, title: label.toUpperCase(),
      }));
    }
    for (const [label, price] of supportLevels) {
      pivotPriceLines.push(candleSeries.createPriceLine({
        price, color: "#16C784", lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true, title: label.toUpperCase(),
      }));
    }
  }

  let fibPriceLines = [];

  function clearFibLines() {
    for (const line of fibPriceLines) candleSeries.removePriceLine(line);
    fibPriceLines = [];
  }

  function drawFibLines(fib) {
    clearFibLines();
    if (!fib) return;
    for (const [ratio, price] of Object.entries(fib.levels)) {
      fibPriceLines.push(candleSeries.createPriceLine({
        price, color: "#f5a623", lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.LargeDashed, axisLabelVisible: true,
        title: `Fib ${Math.round(parseFloat(ratio) * 100)}%`,
      }));
    }
  }

  // Volume profile: a lightweight canvas sidebar (LWC has no native support
  // for this) — one horizontal bar per price bucket, scaled to the busiest
  // bucket, redrawn on render/resize. Non-interactive: it doesn't re-sync
  // while panning/zooming, which is an acceptable v1 simplification.
  const profileCanvas = document.createElement("canvas");
  profileCanvas.id = "volume-profile";
  profileCanvas.style.position = "absolute";
  profileCanvas.style.top = "0";
  profileCanvas.style.right = "0";
  profileCanvas.style.pointerEvents = "none";
  container.style.position = "relative";
  container.appendChild(profileCanvas);
  let currentVolumeProfile = null;

  function drawVolumeProfile() {
    const width = 90;
    const height = container.clientHeight;
    profileCanvas.width = width;
    profileCanvas.height = height;
    profileCanvas.style.width = width + "px";
    profileCanvas.style.height = height + "px";
    const ctx = profileCanvas.getContext("2d");
    ctx.clearRect(0, 0, width, height);
    if (!currentVolumeProfile || !currentVolumeProfile.length) return;
    const maxVolume = Math.max(...currentVolumeProfile.map((b) => b.volume), 1);
    ctx.fillStyle = "rgba(58, 134, 255, 0.35)";
    for (const bin of currentVolumeProfile) {
      const yTop = candleSeries.priceToCoordinate(bin.price_high);
      const yBottom = candleSeries.priceToCoordinate(bin.price_low);
      if (yTop === null || yBottom === null) continue;
      const barWidth = (bin.volume / maxVolume) * width;
      ctx.fillRect(width - barWidth, Math.min(yTop, yBottom), barWidth, Math.max(1, Math.abs(yBottom - yTop)));
    }
  }

  // Infinite scroll-back state: how many candles/how far back we've loaded,
  // and guards against firing overlapping/repeat history requests while one
  // is already in flight or the pagination has run out of older data.
  let currentCandleCount = 0;
  let earliestCandleTime = null;
  let isLoadingMore = false;
  let noMoreHistory = false;

  function renderChart(payload) {
    const candles = payload.candles || [];
    candleSeries.setData(candles);
    volumeSeries.setData(payload.volume || []);
    syncOverlays(payload.overlays || {});
    drawSignalLines(payload.signal || null);
    drawPivotLines(payload.pivots || null);
    drawFibLines(payload.fibonacci || null);
    currentVolumeProfile = payload.volume_profile || null;
    chart.timeScale().fitContent();
    drawVolumeProfile();

    currentCandleCount = candles.length;
    earliestCandleTime = candles.length ? candles[0].time : null;
    isLoadingMore = false;
    noMoreHistory = false;
  }

  function updateLastCandle(payload) {
    if (payload.candle) candleSeries.update(payload.candle);
    if (payload.volume) volumeSeries.update(payload.volume);
  }

  // Prepend older history fetched via pagination. Unlike renderChart, this
  // must NOT call fitContent() — that would jarringly recenter the view.
  // Instead it captures the visible logical range beforehand and restores
  // it shifted by however many bars landed in front of what was visible.
  function prependHistory(payload) {
    const newCandles = payload.candles || [];
    const priorRange = chart.timeScale().getVisibleLogicalRange();
    const delta = newCandles.length - currentCandleCount;

    candleSeries.setData(newCandles);
    volumeSeries.setData(payload.volume || []);
    syncOverlays(payload.overlays || {});
    drawPivotLines(payload.pivots || null);
    drawFibLines(payload.fibonacci || null);
    currentVolumeProfile = payload.volume_profile || null;
    drawVolumeProfile();

    currentCandleCount = newCandles.length;
    earliestCandleTime = newCandles.length ? newCandles[0].time : null;
    isLoadingMore = false;
    if (payload.no_more_history) noMoreHistory = true;

    if (priorRange && delta !== 0) {
      chart.timeScale().setVisibleLogicalRange({
        from: priorRange.from + delta,
        to: priorRange.to + delta,
      });
    }
  }

  // Called by Python when a history request comes back empty/failed, so the
  // user can pan again to retry rather than being stuck forever.
  function resetLoadingMoreFlag() {
    isLoadingMore = false;
  }

  function setNoMoreHistory(value) {
    noMoreHistory = !!value;
  }

  chart.timeScale().subscribeVisibleLogicalRangeChange(function (range) {
    if (!range || isLoadingMore || noMoreHistory) return;
    if (earliestCandleTime === null || !window.bridge) return;
    // Bars are indexed oldest-to-newest starting at 0; `from` nearing 0
    // means the user has panned close to the left (oldest-loaded) edge.
    if (range.from < 20) {
      isLoadingMore = true;
      window.bridge.request_more_history(earliestCandleTime);
    }
  });

  function resize() {
    chart.resize(container.clientWidth, container.clientHeight);
    drawVolumeProfile();
  }
  window.addEventListener("resize", resize);

  // Expose for direct QWebEngineView.page().runJavaScript() calls, and for
  // the QWebChannel bridge signals wired up below.
  window.renderChart = renderChart;
  window.updateLastCandle = updateLastCandle;
  window.prependHistory = prependHistory;
  window.resetLoadingMoreFlag = resetLoadingMoreFlag;
  window.setNoMoreHistory = setNoMoreHistory;

  if (window.qt && window.qt.webChannelTransport) {
    new QWebChannel(window.qt.webChannelTransport, function (channel) {
      window.bridge = channel.objects.bridge;
      window.bridge.render_signal.connect(function (payloadJson) {
        renderChart(JSON.parse(payloadJson));
      });
      window.bridge.update_last_candle_signal.connect(function (payloadJson) {
        updateLastCandle(JSON.parse(payloadJson));
      });
      window.bridge.prepend_history_signal.connect(function (payloadJson) {
        prependHistory(JSON.parse(payloadJson));
      });
      window.bridge.no_more_history_signal.connect(function () {
        setNoMoreHistory(true);
      });
      window.bridge.history_request_failed_signal.connect(function () {
        resetLoadingMoreFlag();
      });
      window.bridge.ready();
    });
  }
})();
