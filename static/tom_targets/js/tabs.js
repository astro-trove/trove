$(document).ready(function() {
  // Handle event tab clicks (from the simple_tag output)
  $(document).on('click', '.event-tab', function(e) {
    e.preventDefault();
    
    // Get the tab index from data-tab attribute
    var tabIndex = $(this).data("tab");
    
    // Find the parent container (in case there are multiple instances)
    var tabsContainer = $(this).closest('.event-tabs-container');
    
    // Remove active class from all tabs in this container
    tabsContainer.find('.event-tab').removeClass("active");
    
    // Hide all cards in this container
    tabsContainer.find('.event-card').removeClass("active").addClass("hidden");
    
    // Add active class to clicked tab
    $(this).addClass("active");
    
    // Show corresponding card
    tabsContainer.find('.event-card[data-tab-content="' + tabIndex + '"]')
      .removeClass("hidden")
      .addClass("active");
  });
});

$(document).ready(function() {
  // Handle event tab clicks (from the simple_tag output)
  $(document).on('click', '.event-subtab', function(e) {
    e.preventDefault();
    
    // Get the tab index from data-tab attribute
    var tabIndex = $(this).data("subtab");
    
    // Find the parent container (in case there are multiple instances)
    var tabsContainer = $(this).closest('.event-subtabs-container');
    
    // Remove active class from all tabs in this container
    tabsContainer.find('.event-subtab').removeClass("active");
    
    // Hide all cards in this container
    tabsContainer.find('.event-subtab-content').removeClass("active").addClass("hidden");
    
    // Add active class to clicked tab
    $(this).addClass("active");
    
    // Show corresponding card
    tabsContainer.find('.event-subtab-content[data-subtab-content="' + tabIndex + '"]')
      .removeClass("hidden")
      .addClass("active");
  });
});
