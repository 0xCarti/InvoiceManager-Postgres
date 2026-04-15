(function () {
    var root = document.getElementById("signage-player");
    if (!root) {
        return;
    }

    var boardCanvas = document.getElementById("board-canvas");
    var manifestUrl = root.getAttribute("data-manifest-url") || "";
    var heartbeatUrl = root.getAttribute("data-heartbeat-url") || "";
    var brandLabel = document.getElementById("brand-label");
    var displayTitle = document.getElementById("display-title");
    var displayLocation = document.getElementById("display-location");
    var playlistName = document.getElementById("playlist-name");
    var slideSource = document.getElementById("slide-source");
    var menuName = document.getElementById("menu-name");
    var menuDescription = document.getElementById("menu-description");
    var pageIndicator = document.getElementById("page-indicator");
    var layoutSummary = document.getElementById("layout-summary");
    var boardContent = document.getElementById("board-content");
    var footerText = document.getElementById("footer-text");
    var slideTimer = document.getElementById("slide-timer");
    var playerStatus = document.getElementById("player-status");

    var showPrices = root.getAttribute("data-show-prices") === "1";
    var showMenuDescription = root.getAttribute("data-show-menu-description") === "1";
    var showPageIndicator = root.getAttribute("data-show-page-indicator") === "1";
    var canvasWidth = Number(root.getAttribute("data-canvas-width") || 1920);
    var canvasHeight = Number(root.getAttribute("data-canvas-height") || 1080);
    var currentLayout = {};
    var slides = [];
    var currentIndex = 0;
    var rotationHandle = null;

    function setStatus(message) {
        if (playerStatus) {
            playerStatus.textContent = message;
        }
    }

    function setText(node, value) {
        if (node) {
            node.textContent = value;
        }
    }

    function setClassHidden(node, hidden) {
        if (!node) {
            return;
        }
        if (hidden) {
            if (node.className.indexOf("hidden") === -1) {
                node.className += " hidden";
            }
            return;
        }
        node.className = node.className.replace(/\s*hidden/g, "");
    }

    function isArray(value) {
        return Object.prototype.toString.call(value) === "[object Array]";
    }

    function escapeHtml(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function formatPrice(value) {
        var number = Number(value || 0);
        if (isNaN(number)) {
            number = 0;
        }
        return "$" + number.toFixed(2);
    }

    function formatRefreshTime() {
        try {
            return new Date().toLocaleTimeString();
        } catch (_error) {
            return new Date().toString();
        }
    }

    function clearRotation() {
        if (rotationHandle) {
            window.clearTimeout(rotationHandle);
            rotationHandle = null;
        }
    }

    function setDescription(value, visible) {
        if (!menuDescription) {
            return;
        }
        setClassHidden(menuDescription, !visible || !value);
        menuDescription.textContent = visible ? (value || "") : "";
    }

    function setPageIndicator(slide) {
        var pageCount = Number(slide && slide.page_count || 0);
        var pageIndex = Number(slide && slide.page_index || 0);
        if (!pageIndicator) {
            return;
        }
        setClassHidden(pageIndicator, !showPageIndicator);
        if (!showPageIndicator) {
            return;
        }
        if (pageCount > 1) {
            pageIndicator.textContent = "Page " + pageIndex + " of " + pageCount;
            return;
        }
        pageIndicator.textContent = "Single Page";
    }

    function resizeCanvas() {
        var availableWidth;
        var availableHeight;
        var width;
        var height;
        var aspectRatio;
        if (!boardCanvas) {
            return;
        }
        availableWidth = Math.max(root.clientWidth - 16, 320);
        availableHeight = Math.max(root.clientHeight - 16, 180);
        width = availableWidth;
        aspectRatio = canvasWidth / canvasHeight;
        height = Math.round(width / aspectRatio);
        if (height > availableHeight) {
            height = availableHeight;
            width = Math.round(height * aspectRatio);
        }
        boardCanvas.style.width = width + "px";
        boardCanvas.style.height = height + "px";
    }

    function buildLayoutSummary(layout) {
        var blockCount = isArray(layout && layout.blocks) ? layout.blocks.length : 0;
        if (layout && layout.uses_blocks && blockCount) {
            return (
                String(blockCount) +
                " blocks / " +
                String(layout.grid_columns || 24) +
                " x " +
                String(layout.grid_rows || 12) +
                " grid"
            );
        }
        return (
            "Columns " + String(layout && layout.board_columns || 3) +
            " / Rows " + String(layout && layout.board_rows || 4)
        );
    }

    function blockPositionStyle(block) {
        return (
            '--block-x:' + String(Math.max(Number(block.grid_x || 1), 1)) + ";" +
            '--block-y:' + String(Math.max(Number(block.grid_y || 1), 1)) + ";" +
            '--block-width:' + String(Math.max(Number(block.grid_width || 12), 1)) + ";" +
            '--block-height:' + String(Math.max(Number(block.grid_height || 4), 1)) + ";"
        );
    }

    function renderEmptyStateHtml(message) {
        return (
            '<div class="block-board">' +
                '<div class="empty-state">' + escapeHtml(message) + "</div>" +
            "</div>"
        );
    }

    function renderClassicContent(slide, layout) {
        var html = "";
        var menu = slide && slide.menu ? slide.menu : {};
        var products = slide && isArray(slide.products) ? slide.products : [];
        var layoutShowPrices = typeof layout.show_prices === "boolean" ? layout.show_prices : showPrices;
        var hasSidePanel = (
            layout.side_panel_position &&
            layout.side_panel_position !== "none" &&
            (layout.side_title || layout.side_body || layout.side_image_url)
        );
        var index;
        var product;

        html += (
            '<div class="menu-layout' +
            (hasSidePanel ? " has-side-panel" : "") +
            '" data-side-panel-position="' +
            escapeHtml(layout.side_panel_position || "none") +
            '">'
        );
        html += '<div class="menu-grid">';
        if (products.length) {
            for (index = 0; index < products.length; index += 1) {
                product = products[index] || {};
                html += (
                    '<article class="menu-item">' +
                        '<div class="menu-item-name">' + escapeHtml(product.name || "") + "</div>" +
                        (
                            layoutShowPrices
                                ? '<div class="menu-item-price">' + formatPrice(product.price) + "</div>"
                                : ""
                        ) +
                    "</article>"
                );
            }
        } else {
            html += (
                '<div class="empty-state">' +
                    escapeHtml(
                        slide
                            ? "No products are available for this menu yet."
                            : "No playlist items are available for this display."
                    ) +
                "</div>"
            );
        }
        html += "</div>";
        if (hasSidePanel) {
            html += '<aside class="side-panel">';
            html += (
                "<div>" +
                    '<div class="player-kicker">Template Panel</div>' +
                    '<h3 class="side-panel-title">' + escapeHtml(layout.side_title || "") + "</h3>" +
                "</div>"
            );
            html += '<div class="side-panel-body">' + escapeHtml(layout.side_body || "") + "</div>";
            if (layout.side_image_url) {
                html += (
                    '<div class="side-panel-image-wrap">' +
                        '<img src="' + escapeHtml(layout.side_image_url) + '" alt="" class="side-panel-image">' +
                    "</div>"
                );
            }
            html += "</aside>";
        }
        html += "</div>";

        if (!menu || !menu.name) {
            return renderEmptyStateHtml("No playlist items are available for this display.");
        }
        return html;
    }

    function blockTypeLabel(blockType) {
        if (blockType === "menu") {
            return "Menu Block";
        }
        if (blockType === "text") {
            return "Text Block";
        }
        if (blockType === "image") {
            return "Image Block";
        }
        if (blockType === "video") {
            return "Video Block";
        }
        return "Board Block";
    }

    function renderMenuBlock(block) {
        var html = "";
        var title = block.title || (block.menu && block.menu.name) || "";
        var products = isArray(block.products) ? block.products : [];
        var index;
        var product;

        html += (
            '<section class="board-block" data-block-type="menu" style="' +
            blockPositionStyle(block) +
            ';">'
        );
        if (block.show_title && title) {
            html += '<div class="player-kicker">' + escapeHtml(blockTypeLabel(block.type)) + "</div>";
            html += '<h3 class="board-block-title">' + escapeHtml(title) + "</h3>";
        }
        if (block.show_menu_description && block.menu && block.menu.description) {
            html += '<p class="board-block-description">' + escapeHtml(block.menu.description) + "</p>";
        }
        html += (
            '<div class="menu-block-grid" style="--block-columns:' +
            String(Math.max(Number(block.menu_columns || 2), 1)) +
            "; --block-rows:" +
            String(Math.max(Number(block.menu_rows || 4), 1)) +
            ';">'
        );
        if (products.length) {
            for (index = 0; index < products.length; index += 1) {
                product = products[index] || {};
                html += (
                    '<article class="menu-block-item">' +
                        '<div class="menu-block-item-name">' + escapeHtml(product.name || "") + "</div>" +
                        (
                            block.show_prices
                                ? '<div class="menu-block-item-price">' + formatPrice(product.price) + "</div>"
                                : ""
                        ) +
                    "</article>"
                );
            }
        } else {
            html += '<div class="empty-state">No products are available for this block.</div>';
        }
        html += "</div>";
        html += "</section>";
        return html;
    }

    function renderTextBlock(block) {
        var html = "";
        var title = block.title || "";

        html += (
            '<section class="board-block" data-block-type="text" style="' +
            blockPositionStyle(block) +
            ';">'
        );
        if (block.show_title && title) {
            html += '<div class="player-kicker">' + escapeHtml(blockTypeLabel(block.type)) + "</div>";
            html += '<h3 class="board-block-title">' + escapeHtml(title) + "</h3>";
        }
        html += '<div class="text-block-body">' + escapeHtml(block.body || "") + "</div>";
        html += "</section>";
        return html;
    }

    function renderImageBlock(block) {
        var html = "";
        var title = block.title || "";

        html += (
            '<section class="board-block" data-block-type="image" style="' +
            blockPositionStyle(block) +
            ';">'
        );
        if (block.show_title && title) {
            html += '<div class="player-kicker">' + escapeHtml(blockTypeLabel(block.type)) + "</div>";
            html += '<h3 class="board-block-title">' + escapeHtml(title) + "</h3>";
        }
        if (block.media_url) {
            html += (
                '<div class="board-block-media-wrap">' +
                    '<img src="' + escapeHtml(block.media_url) + '" alt="" class="image-block-image">' +
                "</div>"
            );
        } else {
            html += '<div class="empty-state">No image has been configured for this block.</div>';
        }
        html += "</section>";
        return html;
    }

    function renderVideoBlock(block) {
        var html = "";
        var title = block.title || "";

        html += (
            '<section class="board-block" data-block-type="video" style="' +
            blockPositionStyle(block) +
            ';">'
        );
        if (block.show_title && title) {
            html += '<div class="player-kicker">' + escapeHtml(blockTypeLabel(block.type)) + "</div>";
            html += '<h3 class="board-block-title">' + escapeHtml(title) + "</h3>";
        }
        if (block.media_url) {
            html += (
                '<div class="board-block-media-wrap">' +
                    '<video class="video-block-video" src="' +
                    escapeHtml(block.media_url) +
                    '" autoplay muted loop playsinline webkit-playsinline></video>' +
                "</div>"
            );
        } else {
            html += '<div class="empty-state">No video has been configured for this block.</div>';
        }
        html += "</section>";
        return html;
    }

    function renderBlockContent(slide) {
        var html = "";
        var blocks = slide && isArray(slide.blocks) ? slide.blocks : [];
        var index;
        var block;

        if (!blocks.length) {
            return renderEmptyStateHtml("No board blocks are configured for this template.");
        }

        html += '<div class="block-board">';
        for (index = 0; index < blocks.length; index += 1) {
            block = blocks[index] || {};
            if (block.type === "menu") {
                html += renderMenuBlock(block);
            } else if (block.type === "image") {
                html += renderImageBlock(block);
            } else if (block.type === "video") {
                html += renderVideoBlock(block);
            } else {
                html += renderTextBlock(block);
            }
        }
        html += "</div>";
        return html;
    }

    function applyLayout(layout, display) {
        var theme;

        currentLayout = layout || {};
        if (layout.board_columns) {
            root.style.setProperty("--board-columns", String(layout.board_columns));
        }
        if (layout.board_rows) {
            root.style.setProperty("--board-rows", String(layout.board_rows));
        }
        if (layout.side_panel_width_percent) {
            root.style.setProperty(
                "--side-panel-width",
                String(Number(layout.side_panel_width_percent || 30)) + "%"
            );
        }
        if (layout.canvas_width) {
            canvasWidth = Number(layout.canvas_width) || 1920;
            root.setAttribute("data-canvas-width", String(canvasWidth));
        }
        if (layout.canvas_height) {
            canvasHeight = Number(layout.canvas_height) || 1080;
            root.setAttribute("data-canvas-height", String(canvasHeight));
        }
        if (typeof layout.show_prices === "boolean") {
            showPrices = layout.show_prices;
            root.setAttribute("data-show-prices", showPrices ? "1" : "0");
        }
        if (typeof layout.show_menu_description === "boolean") {
            showMenuDescription = layout.show_menu_description;
            root.setAttribute(
                "data-show-menu-description",
                showMenuDescription ? "1" : "0"
            );
        }
        if (typeof layout.show_page_indicator === "boolean") {
            showPageIndicator = layout.show_page_indicator;
            root.setAttribute(
                "data-show-page-indicator",
                showPageIndicator ? "1" : "0"
            );
        }
        theme = layout.template && layout.template.theme ? layout.template.theme : "aurora";
        root.setAttribute("data-theme", theme);
        setText(brandLabel, layout.brand_label || "Digital Menu Board");
        setText(displayTitle, layout.brand_name || display.name || "");
        setText(layoutSummary, buildLayoutSummary(layout));
        setText(footerText, layout.footer_text || "");
        resizeCanvas();
    }

    function renderSlide(slide) {
        var menu = slide && slide.menu ? slide.menu : {};
        var durationSeconds = Math.max(Number(slide && slide.duration_seconds || 15), 5);
        var summaryKicker = slide && slide.summary_kicker
            ? slide.summary_kicker
            : (
                slide && slide.source_type === "location_menu"
                    ? "Location Menu"
                    : "Specific Menu"
            );
        var summaryTitle = slide && slide.summary_title
            ? slide.summary_title
            : (menu.name || "No menu assigned");
        var summaryDescription = slide && slide.summary_description
            ? slide.summary_description
            : (menu.description || "");
        var showSummaryDescription = slide && typeof slide.show_summary_description === "boolean"
            ? slide.show_summary_description
            : showMenuDescription;

        setText(slideSource, summaryKicker);
        setText(menuName, summaryTitle || "Nothing Scheduled");
        setDescription(summaryDescription, showSummaryDescription);
        setPageIndicator(slide);
        setText(slideTimer, "Showing for " + durationSeconds + " seconds");

        if (!boardContent) {
            return;
        }
        if ((currentLayout && currentLayout.uses_blocks) || (slide && isArray(slide.blocks) && slide.blocks.length)) {
            boardContent.innerHTML = renderBlockContent(slide);
            return;
        }
        boardContent.innerHTML = renderClassicContent(slide, currentLayout);
    }

    function scheduleNextSlide() {
        var currentSlide;
        clearRotation();
        if (!slides.length) {
            return;
        }
        currentSlide = slides[currentIndex] || {};
        rotationHandle = window.setTimeout(function () {
            currentIndex = (currentIndex + 1) % slides.length;
            renderSlide(slides[currentIndex]);
            scheduleNextSlide();
        }, Math.max(Number(currentSlide.duration_seconds || 15), 5) * 1000);
    }

    function applyManifest(manifest) {
        var display = manifest && manifest.display ? manifest.display : {};
        var layout = manifest && manifest.layout ? manifest.layout : {};
        var playlist = manifest && manifest.playlist ? manifest.playlist : {};

        slides = manifest && isArray(manifest.slides) ? manifest.slides : [];
        currentIndex = 0;

        applyLayout(layout, display);
        setText(displayLocation, display.location_name || "");
        setText(
            playlistName,
            playlist.name ? playlist.name : "Location Menu Fallback"
        );
        setStatus("Last refresh: " + formatRefreshTime());

        if (!slides.length) {
            setText(slideSource, "No Slides");
            setText(menuName, "Nothing Scheduled");
            setDescription("", false);
            setPageIndicator(null);
            setText(slideTimer, "Awaiting playlist data");
            if (boardContent) {
                boardContent.innerHTML = renderEmptyStateHtml(
                    "No playlist items are available for this display."
                );
            }
            clearRotation();
            return;
        }

        renderSlide(slides[currentIndex]);
        scheduleNextSlide();
    }

    function parseJson(text) {
        try {
            return JSON.parse(text);
        } catch (_error) {
            return null;
        }
    }

    function requestJson(url, onSuccess, onFailure) {
        var request = new XMLHttpRequest();
        request.open("GET", url, true);
        request.setRequestHeader("Cache-Control", "no-store");
        request.onreadystatechange = function () {
            var payload;
            if (request.readyState !== 4) {
                return;
            }
            if (request.status >= 200 && request.status < 300) {
                payload = parseJson(request.responseText);
                if (payload) {
                    onSuccess(payload);
                    return;
                }
                onFailure("Unable to parse playlist data");
                return;
            }
            onFailure("Playlist request failed (" + request.status + ")");
        };
        request.onerror = function () {
            onFailure("Playlist request failed");
        };
        request.send(null);
    }

    function sendHeartbeat() {
        var request = new XMLHttpRequest();
        request.open("POST", heartbeatUrl, true);
        request.setRequestHeader("Content-Type", "application/json");
        request.onerror = function () {
            setStatus("Heartbeat failed");
        };
        try {
            request.send("{}");
        } catch (_error) {
            setStatus("Heartbeat failed");
        }
    }

    function refresh() {
        requestJson(
            manifestUrl,
            function (manifest) {
                applyManifest(manifest);
            },
            function (message) {
                setStatus(message);
                if (boardContent) {
                    boardContent.innerHTML = renderEmptyStateHtml(
                        "The player could not load its playlist."
                    );
                }
                clearRotation();
            }
        );
    }

    if (window.addEventListener) {
        window.addEventListener("resize", resizeCanvas);
    } else if (window.attachEvent) {
        window.attachEvent("onresize", resizeCanvas);
    }

    resizeCanvas();
    refresh();
    sendHeartbeat();
    window.setInterval(refresh, 60000);
    window.setInterval(sendHeartbeat, 30000);
}());
