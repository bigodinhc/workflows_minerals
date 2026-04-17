export interface WorkflowRun {
  conclusion: string | null;
  created_at: string;
}

export interface WorkflowLastRun {
  status: string;
  conclusion: string | null;
  created_at: string;
  duration_seconds: number | null;
}

export interface Workflow {
  id: string;
  name: string;
  description: string;
  icon: string;
  last_run: WorkflowLastRun | null;
  health_pct: number;
  recent_runs: WorkflowRun[];
}

export interface WorkflowsResponse {
  workflows: Workflow[];
}

export interface RunDetail {
  id: number;
  status: string;
  conclusion: string | null;
  created_at: string;
  duration_seconds: number | null;
  error: string | null;
  html_url: string;
}

export interface RunsResponse {
  runs: RunDetail[];
}

export interface NewsItem {
  id: string;
  title: string;
  source: string;
  source_feed: string;
  date: string;
  status: "pending" | "archived" | "rejected";
  preview_url: string | null;
}

export interface NewsResponse {
  items: NewsItem[];
  total: number;
  page: number;
}

export interface NewsDetail {
  id: string;
  title: string;
  source: string;
  source_feed: string;
  date: string;
  status: string;
  fullText: string;
  tables: Array<{ header: string[]; rows: string[][] }>;
  preview_url: string;
}

export interface Report {
  id: string;
  report_name: string;
  date_key: string;
  download_url: string;
}

export interface ReportsResponse {
  reports: Report[];
}

export interface Contact {
  name: string;
  phone: string;
  active: boolean;
}

export interface ContactsResponse {
  contacts: Contact[];
  total: number;
  page: number;
}

export interface Stats {
  health_pct: number;
  workflows_ok: number;
  workflows_total: number;
  runs_today: number;
  contacts_active: number;
  news_today: number;
}
