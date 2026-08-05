import { Pause, SkipBack, SkipForward } from "lucide-react";

const days = ["21", "22", "23", "24", "25", "26", "27", "28"];

export function Timeline() {
  return (
    <section className="timeline" aria-label="足迹时间轴">
      <div className="timeline-toolbar">
        <div className="timeline-title">
          <span>时间轴</span>
          <strong>2026 年 7 月 21 日 — 28 日</strong>
        </div>
        <div className="playback-controls">
          <button aria-label="上一段"><SkipBack size={14} /></button>
          <button className="play-button" aria-label="暂停"><Pause size={14} /></button>
          <button aria-label="下一段"><SkipForward size={14} /></button>
        </div>
        <div className="timeline-summary"><span />7 天 · 186.4 公里</div>
      </div>

      <div className="timeline-chart">
        <div className="timeline-labels">
          <span>移动</span>
          <span>停留</span>
        </div>
        <div className="timeline-plot">
          <div className="timeline-grid" />
          <div className="activity-row movement-row">
            <i style={{ left: "3%", width: "12%" }} />
            <i style={{ left: "21%", width: "18%" }} />
            <i style={{ left: "45%", width: "8%" }} />
            <i style={{ left: "66%", width: "27%" }} />
          </div>
          <div className="activity-row stay-row">
            <i style={{ left: "1%", width: "16%" }} />
            <i style={{ left: "36%", width: "12%" }} />
            <i style={{ left: "52%", width: "18%" }} />
            <i style={{ left: "89%", width: "9%" }} />
          </div>
          <div className="selection-window"><b /><b /></div>
          <div className="playhead"><span>24 日 16:30</span></div>
          <div className="timeline-days">
            {days.map((day) => <span key={day}>{day}</span>)}
          </div>
        </div>
      </div>
    </section>
  );
}
