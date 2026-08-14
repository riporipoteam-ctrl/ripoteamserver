#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <pdh.h>
#include <stdint.h>
#include <string.h>
#include <wchar.h>

/*
 * Ripo LIVE Studio Wine compatibility shim.
 *
 * TikTok LIVE Studio probes Windows GPU performance counters through PDH.
 * Wine does not currently expose the GPU counter objects LIVE Studio asks for,
 * causing PdhAddCounterA/W to return PDH_CSTATUS_NO_COUNTER and the app to exit
 * before creating its UI. This DLL provides a deliberately tiny, read-only PDH
 * surface that reports valid zero-valued counters. It is used only for the LIVE
 * Studio process via WINEDLLOVERRIDES=pdh=n,b.
 */

#define RIPO_MAGIC 0x52504448u

typedef struct RipoPdhHandle {
    uint32_t magic;
    uint32_t kind;
} RipoPdhHandle;

static RipoPdhHandle *ripo_new_handle(uint32_t kind) {
    RipoPdhHandle *h = (RipoPdhHandle *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(*h));
    if (h) {
        h->magic = RIPO_MAGIC;
        h->kind = kind;
    }
    return h;
}

static BOOL ripo_valid(void *ptr) {
    RipoPdhHandle *h = (RipoPdhHandle *)ptr;
    return h && h->magic == RIPO_MAGIC;
}

static void ripo_free(void *ptr) {
    if (ripo_valid(ptr)) {
        ((RipoPdhHandle *)ptr)->magic = 0;
        HeapFree(GetProcessHeap(), 0, ptr);
    }
}

static void ripo_fill_fmt(DWORD format, PPDH_FMT_COUNTERVALUE value) {
    if (!value) return;
    ZeroMemory(value, sizeof(*value));
    value->CStatus = ERROR_SUCCESS;
    if (format & PDH_FMT_DOUBLE) value->doubleValue = 0.0;
    else if (format & PDH_FMT_LARGE) value->largeValue = 0;
    else value->longValue = 0;
}

__declspec(dllexport) PDH_STATUS WINAPI PdhOpenQueryA(LPCSTR source, DWORD_PTR userdata, PDH_HQUERY *query) {
    (void)source; (void)userdata;
    if (!query) return PDH_INVALID_ARGUMENT;
    *query = (PDH_HQUERY)ripo_new_handle(1);
    return *query ? ERROR_SUCCESS : PDH_MEMORY_ALLOCATION_FAILURE;
}

__declspec(dllexport) PDH_STATUS WINAPI PdhOpenQueryW(LPCWSTR source, DWORD_PTR userdata, PDH_HQUERY *query) {
    (void)source; (void)userdata;
    if (!query) return PDH_INVALID_ARGUMENT;
    *query = (PDH_HQUERY)ripo_new_handle(1);
    return *query ? ERROR_SUCCESS : PDH_MEMORY_ALLOCATION_FAILURE;
}

__declspec(dllexport) PDH_STATUS WINAPI PdhCloseQuery(PDH_HQUERY query) {
    if (!query) return PDH_INVALID_HANDLE;
    ripo_free((void *)query);
    return ERROR_SUCCESS;
}

static PDH_STATUS ripo_add(PDH_HQUERY query, PDH_HCOUNTER *counter) {
    if (!query || !counter) return PDH_INVALID_ARGUMENT;
    *counter = (PDH_HCOUNTER)ripo_new_handle(2);
    return *counter ? ERROR_SUCCESS : PDH_MEMORY_ALLOCATION_FAILURE;
}

__declspec(dllexport) PDH_STATUS WINAPI PdhAddCounterA(PDH_HQUERY query, LPCSTR path, DWORD_PTR userdata, PDH_HCOUNTER *counter) {
    (void)path; (void)userdata;
    return ripo_add(query, counter);
}

__declspec(dllexport) PDH_STATUS WINAPI PdhAddCounterW(PDH_HQUERY query, LPCWSTR path, DWORD_PTR userdata, PDH_HCOUNTER *counter) {
    (void)path; (void)userdata;
    return ripo_add(query, counter);
}

__declspec(dllexport) PDH_STATUS WINAPI PdhAddEnglishCounterA(PDH_HQUERY query, LPCSTR path, DWORD_PTR userdata, PDH_HCOUNTER *counter) {
    return PdhAddCounterA(query, path, userdata, counter);
}

__declspec(dllexport) PDH_STATUS WINAPI PdhAddEnglishCounterW(PDH_HQUERY query, LPCWSTR path, DWORD_PTR userdata, PDH_HCOUNTER *counter) {
    return PdhAddCounterW(query, path, userdata, counter);
}

__declspec(dllexport) PDH_STATUS WINAPI PdhRemoveCounter(PDH_HCOUNTER counter) {
    if (!counter) return PDH_INVALID_HANDLE;
    ripo_free((void *)counter);
    return ERROR_SUCCESS;
}

__declspec(dllexport) PDH_STATUS WINAPI PdhCollectQueryData(PDH_HQUERY query) {
    return query ? ERROR_SUCCESS : PDH_INVALID_HANDLE;
}

__declspec(dllexport) PDH_STATUS WINAPI PdhCollectQueryDataWithTime(PDH_HQUERY query, LONGLONG *timeValue) {
    if (!query) return PDH_INVALID_HANDLE;
    if (timeValue) {
        FILETIME ft;
        GetSystemTimeAsFileTime(&ft);
        *timeValue = ((LONGLONG)ft.dwHighDateTime << 32) | ft.dwLowDateTime;
    }
    return ERROR_SUCCESS;
}

__declspec(dllexport) PDH_STATUS WINAPI PdhGetFormattedCounterValue(PDH_HCOUNTER counter, DWORD format, LPDWORD type, PPDH_FMT_COUNTERVALUE value) {
    if (!counter || !value) return PDH_INVALID_ARGUMENT;
    if (type) *type = PERF_COUNTER_RAWCOUNT;
    ripo_fill_fmt(format, value);
    return ERROR_SUCCESS;
}

__declspec(dllexport) PDH_STATUS WINAPI PdhGetRawCounterValue(PDH_HCOUNTER counter, LPDWORD type, PPDH_RAW_COUNTER value) {
    if (!counter || !value) return PDH_INVALID_ARGUMENT;
    if (type) *type = PERF_COUNTER_RAWCOUNT;
    ZeroMemory(value, sizeof(*value));
    value->CStatus = ERROR_SUCCESS;
    GetSystemTimeAsFileTime(&value->TimeStamp);
    value->FirstValue = 0;
    value->SecondValue = 1;
    value->MultiCount = 1;
    return ERROR_SUCCESS;
}

__declspec(dllexport) PDH_STATUS WINAPI PdhGetFormattedCounterArrayA(PDH_HCOUNTER counter, DWORD format, LPDWORD bufferSize, LPDWORD itemCount, PPDH_FMT_COUNTERVALUE_ITEM_A items) {
    static char name[] = "RipoGPU";
    if (!counter || !bufferSize || !itemCount) return PDH_INVALID_ARGUMENT;
    if (!items || *bufferSize < sizeof(PDH_FMT_COUNTERVALUE_ITEM_A)) {
        *bufferSize = sizeof(PDH_FMT_COUNTERVALUE_ITEM_A);
        *itemCount = 1;
        return PDH_MORE_DATA;
    }
    *itemCount = 1;
    items[0].szName = name;
    ripo_fill_fmt(format, &items[0].FmtValue);
    return ERROR_SUCCESS;
}

__declspec(dllexport) PDH_STATUS WINAPI PdhGetFormattedCounterArrayW(PDH_HCOUNTER counter, DWORD format, LPDWORD bufferSize, LPDWORD itemCount, PPDH_FMT_COUNTERVALUE_ITEM_W items) {
    static WCHAR name[] = L"RipoGPU";
    if (!counter || !bufferSize || !itemCount) return PDH_INVALID_ARGUMENT;
    if (!items || *bufferSize < sizeof(PDH_FMT_COUNTERVALUE_ITEM_W)) {
        *bufferSize = sizeof(PDH_FMT_COUNTERVALUE_ITEM_W);
        *itemCount = 1;
        return PDH_MORE_DATA;
    }
    *itemCount = 1;
    items[0].szName = name;
    ripo_fill_fmt(format, &items[0].FmtValue);
    return ERROR_SUCCESS;
}

__declspec(dllexport) PDH_STATUS WINAPI PdhSetCounterScaleFactor(PDH_HCOUNTER counter, LONG factor) {
    (void)factor;
    return counter ? ERROR_SUCCESS : PDH_INVALID_HANDLE;
}

__declspec(dllexport) PDH_STATUS WINAPI PdhValidatePathA(LPCSTR path) {
    return (path && *path) ? ERROR_SUCCESS : PDH_CSTATUS_BAD_COUNTERNAME;
}

__declspec(dllexport) PDH_STATUS WINAPI PdhValidatePathW(LPCWSTR path) {
    return (path && *path) ? ERROR_SUCCESS : PDH_CSTATUS_BAD_COUNTERNAME;
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved) {
    (void)instance; (void)reason; (void)reserved;
    return TRUE;
}
