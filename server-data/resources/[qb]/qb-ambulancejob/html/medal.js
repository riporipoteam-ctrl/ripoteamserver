window.addEventListener('message', (event) => {
    const data = event.data;
    if (!data || data.action !== 'medal:invoke') return;

    fetch(data.endpoint, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json; charset=utf-8',
            publicKey: data.publicKey
        },
        body: JSON.stringify(data.body)
    }).catch(() => {});
});
