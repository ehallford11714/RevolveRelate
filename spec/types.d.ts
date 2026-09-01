/** Language-agnostic RelOp / schema types. Both runtimes implement these. */

export type Sensitivity = "public" | "internal" | "critical" | "pii";

export type RelOpKind =
  | "scan"
  | "project"
  | "filter"
  | "join"
  | "aggregate"
  | "window"
  | "sort"
  | "limit"
  | "distinct"
  | "setop"
  | "values"
  | "with"
  | "insert"
  | "update"
  | "delete"
  | "merge"
  | "call";

export type DocumentKind = "query" | "mutate" | "txn" | "procedure";

export interface RelationalDocument {
  kind: DocumentKind;
  op?: { op: RelOpKind; [key: string]: unknown };
  statements?: RelationalDocument[];
  name?: string;
  onError?: "rollback" | "abort";
}
