export function formatPdfCitationContent(content: string, headingPath = ''): string {
  const lines = content.split('\n');
  const firstContent = lines.findIndex(line => line.trim());
  if (firstContent >= 0 && headingPath && normalize(lines[firstContent]!) === normalize(headingPath)) {
    lines.splice(firstContent, 1);
    if (lines[firstContent] !== undefined && !lines[firstContent]!.trim()) lines.splice(firstContent, 1);
  }

  const output: string[] = [];
  for (let index = 0; index < lines.length;) {
    if (!pipeCells(lines[index]!)) {
      output.push(lines[index]!);
      index += 1;
      continue;
    }
    const rows: string[][] = [];
    while (index < lines.length) {
      const row = pipeCells(lines[index]!);
      if (!row) break;
      rows.push(row);
      index += 1;
    }
    if (looksLikeTable(rows)) {
      const width = Math.max(...rows.map(row => row.length));
      const padded = rows.map(row => [...row, ...Array<string>(width - row.length).fill('')]);
      output.push(`| ${padded[0]!.join(' | ')} |`);
      output.push(`| ${Array<string>(width).fill('---').join(' | ')} |`);
      output.push(...padded.slice(1).map(row => `| ${row.join(' | ')} |`));
    } else {
      output.push(...rows.map(row => row.filter(Boolean).join(' ').trim()));
    }
  }
  return output.join('\n').trim() || content;
}

function normalize(value: string): string {
  return value.replace(/\s+/g, ' ').trim().toLocaleLowerCase();
}

function pipeCells(line: string): string[] | undefined {
  const stripped = line.trim();
  if (stripped.length < 2 || !stripped.startsWith('|') || !stripped.endsWith('|')) return undefined;
  const cells = stripped.slice(1, -1).split('|').map(cell => cell.trim());
  return cells.some(Boolean) ? cells : undefined;
}

function looksLikeTable(rows: string[][]): boolean {
  if (rows.length < 2) return false;
  const counts = rows.map(row => row.length);
  const cells = rows.flat().filter(Boolean);
  if (Math.min(...counts) < 2 || Math.max(...counts) - Math.min(...counts) > 1 || !cells.length) return false;
  const lengths = cells.map(cell => cell.length).sort((left, right) => left - right);
  const middle = Math.floor(lengths.length / 2);
  const median = lengths.length % 2 ? lengths[middle]! : (lengths[middle - 1]! + lengths[middle]!) / 2;
  if (Math.max(...lengths) > 80 || median > 32) return false;
  const prosePunctuation = cells.reduce((count, cell) => count + (cell.match(/[。；]/g)?.length ?? 0), 0);
  return prosePunctuation < rows.length;
}
