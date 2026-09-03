import { MigrationEmptyState } from "../components/MigrationEmptyState/MigrationEmptyState";

type MigrationPageProps = {
  heading: string;
};

export function MigrationPage({ heading }: MigrationPageProps) {
  return (
    <article className="migration-page">
      <h1>{heading}</h1>
      <MigrationEmptyState />
    </article>
  );
}
