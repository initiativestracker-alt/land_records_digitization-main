// Convert extraction-only manual_sheets.json to the six-sheet capture workbook.
import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const [input, output] = process.argv.slice(2);
if (!input || !output) throw new Error('Usage: node export_manual.mjs manual_sheets.json output.xlsx');
const matrices = JSON.parse(await fs.readFile(input, 'utf8'));
const workbook = Workbook.create();
for (const [name, values] of Object.entries(matrices)) {
  const sheet = workbook.worksheets.add(name);
  const height = values.length, width = values[0].length;
  sheet.getRangeByIndexes(0, 0, height, width).values = values;
  const all = sheet.getRangeByIndexes(0, 0, height, width);
  all.format.font = { name: 'Arial', size: 10 };
  all.format.rowHeight = 30;
  all.format.wrapText = true;
  sheet.getRangeByIndexes(0, 0, height, 1).format.columnWidth = 5;
  sheet.getRangeByIndexes(0, 1, height, 1).format.columnWidth = 44;
  sheet.getRangeByIndexes(0, 2, height, 1).format.columnWidth = 42;
  if (width > 3) sheet.getRangeByIndexes(0, 3, height, width - 3).format.columnWidth = 28;
  sheet.getRangeByIndexes(0, 0, 3, width).format = {fill:'#E7EDF1',font:{name:'Arial',size:10,bold:true},rowHeight:24};
  sheet.getRangeByIndexes(3, 0, height - 3, 3).format.fill = '#F3F5F6';
  // Long table names and multi-value fields remain readable at normal zoom.
  for (let row = 3; row < height; row++) {
    const lines = Math.max(...values[row].map((v, col) => Math.ceil(String(v ?? '').length / (col === 1 ? 34 : col === 2 ? 36 : 24))));
    sheet.getRangeByIndexes(row, 0, 1, width).format.rowHeight = Math.max(30, (lines + 1) * 16);
  }
  sheet.freezePanes.freezeRows(3);
  sheet.freezePanes.freezeColumns(3);
}
workbook.recalculate();
await fs.mkdir(path.dirname(output), {recursive:true});
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(output);
for (const name of Object.keys(matrices)) {
  const preview = await workbook.render({sheetName:name,range:`A1:F${matrices[name].length}`,scale:1,format:'png'});
  await fs.writeFile(path.join(path.dirname(output),`${name.replaceAll(' ','_')}.png`),new Uint8Array(await preview.arrayBuffer()));
}
console.log(JSON.stringify({output,sheets:Object.fromEntries(Object.entries(matrices).map(([name,rows])=>[name,{rows:rows.length,entries:rows[0].length-3}]))}));
