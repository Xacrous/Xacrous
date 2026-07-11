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

  const overlayColors = { sma20: "#f5a623", sma50: "#3a86ff", sma200: "#a259ff" };
  const overlaySeries = {};
  for (const key of Object.keys(overlayColors)) {
    overlaySeries[key] = chart.addLineSeries({
      color: overlayColors[key],
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });
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

  function renderChart(payload) {
    candleSeries.setData(payload.candles || []);
    volumeSeries.setData(payload.volume || []);
    const overlays = payload.overlays || {};
    for (const key of Object.keys(overlaySeries)) {
      overlaySeries[key].setData(overlays[key] || []);
    }
    drawSignalLines(payload.signal || null);
    chart.timeScale().fitContent();
  }

  function updateLastCandle(payload) {
    if (payload.candle) candleSeries.update(payload.candle);
    if (payload.volume) volumeSeries.update(payload.volume);
  }

  function resize() {
    chart.resize(container.clientWidth, container.clientHeight);
  }
  window.addEventListener("resize", resize);

  // Expose for direct QWebEngineView.page().runJavaScript() calls, and for
  // the QWebChannel bridge signals wired up below.
  window.renderChart = renderChart;
  window.updateLastCandle = updateLastCandle;

  if (window.qt && window.qt.webChannelTransport) {
    new QWebChannel(window.qt.webChannelTransport, function (channel) {
      window.bridge = channel.objects.bridge;
      window.bridge.render_signal.connect(function (payloadJson) {
        renderChart(JSON.parse(payloadJson));
      });
      window.bridge.update_last_candle_signal.connect(function (payloadJson) {
        updateLastCandle(JSON.parse(payloadJson));
      });
      window.bridge.ready();
    });
  }
})();
