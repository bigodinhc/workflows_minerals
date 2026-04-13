"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, CheckCircle2, XCircle } from "lucide-react";

type DeliveryResult = {
  name: string;
  phone: string;
  success: boolean;
  error: string | null;
  duration_ms: number;
};

type DeliveryReport = {
  workflow: string;
  started_at: string;
  finished_at: string;
  duration_seconds: number;
  summary: { total: number; success: number; failure: number };
  results: DeliveryResult[];
};

export function DeliveryReportView({ report }: { report: DeliveryReport }) {
  const [showSuccesses, setShowSuccesses] = useState(false);
  const failures = report.results.filter((r) => !r.success);
  const successes = report.results.filter((r) => r.success);

  const failPct = report.summary.total
    ? (report.summary.failure / report.summary.total) * 100
    : 0;
  const statusEmoji = report.summary.failure === 0 ? "✅" : failPct > 50 ? "🚨" : "⚠️";
  const statusColor =
    report.summary.failure === 0
      ? "text-[#00FF41]"
      : failPct > 50
      ? "text-[#ff3333]"
      : "text-[#FFD700]";

  return (
    <div className="border border-[#1a1a1a] bg-[#0a0a0a] p-4 mb-4">
      <div className="flex items-center gap-2 mb-3">
        <span className={`text-lg ${statusColor}`}>{statusEmoji}</span>
        <p className="text-[11px] text-[#00FF41] uppercase tracking-[0.2em]">
          / DELIVERY REPORT
        </p>
        <div className="flex-1 h-px bg-[#1a1a1a]" />
        <span className="text-[10px] text-[#555] uppercase">
          {report.workflow.replace(/_/g, " ")}
        </span>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-3 gap-3 mb-4">
        <div className="border border-[#1a1a1a] bg-[#050505] p-3">
          <p className="text-[9px] text-[#555] uppercase">TOTAL</p>
          <p className="text-2xl text-white font-bold">{report.summary.total}</p>
        </div>
        <div className="border border-[#00FF41]/20 bg-[#050505] p-3">
          <p className="text-[9px] text-[#555] uppercase">OK</p>
          <p className="text-2xl text-[#00FF41] font-bold">{report.summary.success}</p>
        </div>
        <div
          className={`border ${
            report.summary.failure > 0 ? "border-[#ff3333]/30" : "border-[#1a1a1a]"
          } bg-[#050505] p-3`}
        >
          <p className="text-[9px] text-[#555] uppercase">FALHA</p>
          <p
            className={`text-2xl font-bold ${
              report.summary.failure > 0 ? "text-[#ff3333]" : "text-[#555]"
            }`}
          >
            {report.summary.failure}
          </p>
        </div>
      </div>

      {/* Failures */}
      {failures.length > 0 && (
        <div className="mb-4">
          <p className="text-[10px] text-[#ff3333] uppercase tracking-wider mb-2">
            / FALHAS ({failures.length})
          </p>
          <div className="border border-[#ff3333]/20 bg-[#050505]">
            {failures.map((r, i) => (
              <div
                key={i}
                className="grid grid-cols-12 gap-2 px-3 py-1.5 text-[11px] border-b border-[#1a1a1a] last:border-0"
              >
                <div className="col-span-4 text-white truncate">{r.name}</div>
                <div className="col-span-4 text-[#555] truncate">{r.phone}</div>
                <div className="col-span-4 text-[#ff3333] truncate">{r.error}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Successes (collapsible) */}
      {successes.length > 0 && (
        <div>
          <button
            onClick={() => setShowSuccesses((v) => !v)}
            className="flex items-center gap-1 text-[10px] text-[#00FF41]/70 uppercase tracking-wider mb-2 hover:text-[#00FF41]"
          >
            {showSuccesses ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            / SUCESSOS ({successes.length})
          </button>
          {showSuccesses && (
            <div className="border border-[#00FF41]/20 bg-[#050505] max-h-60 overflow-y-auto">
              {successes.map((r, i) => (
                <div
                  key={i}
                  className="grid grid-cols-12 gap-2 px-3 py-1.5 text-[11px] border-b border-[#1a1a1a] last:border-0"
                >
                  <div className="col-span-6 text-white truncate">{r.name}</div>
                  <div className="col-span-4 text-[#555] truncate">{r.phone}</div>
                  <div className="col-span-2 text-[#00FF41] text-right">
                    {r.duration_ms}ms
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
