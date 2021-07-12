import { get, writable, Writable } from 'svelte/store';
import { getAPI, States } from '@mathesar/utils/api';
import type { CancellablePromise } from '@mathesar/components';

export interface TableColumn {
  name: string,
  type: string
}

interface TableRecords {
  [key: string]: unknown
}

interface TableDetailsResponse {
  columns: TableColumn[]
}

interface TableRecordsResponse {
  count: number,
  results: TableRecords[]
}

export interface TableColumnData {
  state: States,
  error?: string,
  data: TableColumn[]
}

interface TableRecordData {
  state: States,
  error?: string,
  data: TableRecords[],
  totalCount: number
}

export type SortOption = Map<string, 'asc' | 'desc'>;
export interface TableOptionsData {
  pageSize: number,
  page: number,
  sort: SortOption
}

interface TableConfigData {
  previousTableRequest?: CancellablePromise<TableDetailsResponse>,
  previousRecordRequest?: CancellablePromise<TableRecordsResponse>,
}

export type TableColumnStore = Writable<TableColumnData>;
export type TableRecordStore = Writable<TableRecordData>;
export type TableOptionsStore = Writable<TableOptionsData>;

interface TableData {
  // Store objects: For use in views and controller
  columns?: TableColumnStore,
  records?: TableRecordStore,
  options?: TableOptionsStore,

  // Direct objects: For use only in controller
  config?: TableConfigData,
}

const databaseMap: Map<string, Map<number, TableData>> = new Map();

async function fetchTableDetails(db: string, id: number): Promise<void> {
  const table = databaseMap.get(db)?.get(id);
  if (table) {
    const tableColumnStore = databaseMap.get(db)?.get(id)?.columns;
    const existingData = get(tableColumnStore);

    tableColumnStore.set({
      state: States.Loading,
      data: existingData.data,
    });

    try {
      table.config.previousTableRequest?.cancel();

      const tableDetailsPromise = getAPI<TableDetailsResponse>(`/tables/${id}/`);
      table.config = {
        ...table.config,
        previousTableRequest: tableDetailsPromise,
      };

      const response = await tableDetailsPromise;
      const columns = response.columns || [];
      tableColumnStore.set({
        state: States.Done,
        data: columns,
      });
    } catch (err) {
      tableColumnStore.set({
        state: States.Error,
        error: err instanceof Error ? err.message : null,
        data: [],
      });
    }
  }
}

export async function fetchTableRecords(
  db: string,
  id: number,
): Promise<void> {
  const table = databaseMap.get(db)?.get(id);
  if (table) {
    const tableRecordStore = table.records;
    const optionStore = table.options;

    const existingData = get(tableRecordStore);
    const optionData = get(optionStore);

    tableRecordStore.set({
      state: States.Loading,
      data: existingData.data,
      totalCount: existingData.totalCount,
    });

    const params = [];
    params.push(`limit=${optionData.pageSize}`);
    const offset = optionData.pageSize * (optionData.page - 1);
    params.push(`offset=${offset}`);

    const sortOptions = [];
    optionData.sort?.forEach((value, key) => {
      sortOptions.push({
        field: key,
        direction: value,
      });
    });
    if (sortOptions.length > 0) {
      params.push(`order_by=${encodeURIComponent(JSON.stringify(sortOptions))}`);
    }

    try {
      table.config.previousRecordRequest?.cancel();

      const tableRecordsPromise = getAPI<TableRecordsResponse>(`/tables/${id}/records/?${params.join('&')}`);
      table.config = {
        ...table.config,
        previousRecordRequest: tableRecordsPromise,
      };

      const response = await tableRecordsPromise;
      const totalCount = response.count || 0;
      const data = response.results || [];
      tableRecordStore.set({
        state: States.Done,
        data,
        totalCount,
      });
    } catch (err) {
      tableRecordStore.set({
        state: States.Error,
        error: err instanceof Error ? err.message : null,
        data: [],
        totalCount: 0,
      });
    }
  }
}

export function getTable(db: string, id: number, options?: Partial<TableOptionsData>): TableData {
  let database = databaseMap.get(db);
  if (!database) {
    database = new Map();
    databaseMap.set(db, database);
  }

  let table = database.get(id);
  if (!table) {
    table = {
      columns: writable({
        state: States.Loading,
        data: [],
      }),
      records: writable({
        state: States.Loading,
        data: [],
        totalCount: 0,
      }),
      options: writable({
        pageSize: options?.pageSize || 50,
        page: options?.page || 1,
        sort: options?.sort || null,
      }),
      config: {},
    };
    database.set(id, table);
    void fetchTableDetails(db, id);
  }
  void fetchTableRecords(db, id);
  return table;
}

export function clearTable(db: string, id: number): void {
  databaseMap.get(db)?.delete(id);
}
